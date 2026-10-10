"""Install the real plugin and initialize fixture-only settings in the interactive guest."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

if __package__:
    from .guest_support import (
        OmvConfiguration,
        OmvDestination,
        ProtonDriveRpc,
        debian_package_name,
        decode,
        interactive_fixture,
        items,
        mapping,
        run,
        string,
    )
else:
    from guest_support import (
        OmvConfiguration,
        OmvDestination,
        ProtonDriveRpc,
        debian_package_name,
        decode,
        interactive_fixture,
        items,
        mapping,
        run,
        string,
    )
from xml.etree.ElementTree import Element, SubElement, tostring

INSTALL_MODULES = ('monit', 'nginx', 'phpfpm', 'protondrive')
DIRTY_MODULES = Path('/var/lib/openmediavault/dirtymodules.json')


def setup_traceback():
    # Older VM disks acquire Rich during the first install; keep bootstrap errors readable.
    try:
        from console import install_traceback
    except ModuleNotFoundError as error:
        if error.name != 'rich':
            raise
    else:
        install_traceback()


def initialize(configuration: Path) -> None:
    settings = interactive_fixture(decode(configuration.read_text()))
    rpc = ProtonDriveRpc(verbose=True)
    # Send the initial web password over stdin, never through argv or logs.
    run('chpasswd', input=f'admin:{settings["admin_password"]}\n', text=True)
    for device in ('desktop', 'mobile'):
        run(
            'omv-rpc',
            '-u',
            'admin',
            'WebGui',
            'setLocalStorageItem',
            json.dumps({'devicetype': device, 'key': 'prefers-color-scheme', 'value': 'dark'}),
        )
    plugin = rpc.get_configuration()
    plugin.update(enable=False, remotepath=settings['remote_folder'])
    rpc.save_configuration(plugin)
    destination = OmvDestination(
        id='protondrive', kind='protondrive', name='Proton Drive', enable=True, root=plugin['remotepath']
    )
    explicit: OmvConfiguration = {**plugin, 'destinations': json.dumps([destination])}
    rpc.save_configuration(explicit)
    rpc.save_configuration(plugin)
    for item in rpc.list_sets():
        if item['name'] not in ('system', 'appData'):
            raise RuntimeError('Unexpected backup set in fresh interactive VM')
        directory = Path('/data/interactive-fixtures') / item['name']
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'example.txt').write_text('Disposable OMV integration fixture.\n')
        item.update(paths=str(directory), excludes='', stopcontainers=False)
        rpc.save_set(item)
    configuration.unlink()


def wait_for_monit(timeout: float = 90) -> None:
    """The control socket can respond before a reload has registered its services."""
    print('Waiting for Monit to register nginx and php-fpm', flush=True)
    deadline = time.monotonic() + timeout
    while True:
        for service in ('nginx', 'php-fpm'):
            result = subprocess.run(
                ['monit', 'status', service], capture_output=True, text=True, timeout=5, check=False
            )
            if result.returncode:
                break
        else:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Monit did not become ready: {result.stdout}{result.stderr}')
        time.sleep(1)


def monit_reload_pending(message: str) -> bool:
    """Retry only a single failed monitor state whose service is not registered yet."""
    if not re.search(r'(?m)^Failed:\s+1\s*$', message):
        return False
    for state, service in (('monitor_nginx_service', 'nginx'), ('monitor_phpfpm_service', 'php-fpm')):
        failed_monitor = rf'ID: {state}\s+Function: module.run\s+Result: False\s+Comment:.*monit.monitor'
        unavailable = (
            f'There is no service named "{service}"' in message
            or 'Unix socket /run/monit.sock connection error -- No such file or directory' in message
        )
        if re.search(failed_monitor, message) and unavailable:
            return True
    return False


def apply_configuration(*modules: str) -> None:
    """Keep rollback history until the full apply succeeds, including reload retries."""
    for attempt in range(3):
        try:
            run(
                'omv-rpc',
                '-u',
                'admin',
                'Config',
                'applyChanges',
                json.dumps({'modules': modules, 'force': True}),
                capture_output=True,
                text=True,
            )
            return
        except subprocess.CalledProcessError as error:
            output = cast(object, error.stdout) or cast(object, error.stderr)
            message = output if isinstance(output, str) else str(error)
            try:
                response = decode(message)
                message = string(mapping(response['error'])['message'])
            except (ValueError, KeyError, TypeError):
                pass
            if attempt == 2 or not monit_reload_pending(message):
                raise RuntimeError(f'OMV configuration apply failed:\n{message}') from None
            print('WARNING: Monit is still reloading; waiting before retrying the full configuration apply', flush=True)
            wait_for_monit()
            check_pending_changes()


def check_pending_changes():
    """OMV discards all rollback revisions on apply, even for a subset of modules."""
    try:
        content = DIRTY_MODULES.read_text()
    except FileNotFoundError:
        # Like OMV's getDirtyModules(), a missing file means no pending changes.
        return
    try:
        pending = [string(module) for module in items(cast(object, json.loads(content)))]
    except (ValueError, TypeError) as error:
        raise RuntimeError('Invalid OMV pending configuration state') from error
    unrelated = set(pending).difference(INSTALL_MODULES)
    if unrelated:
        raise RuntimeError(f'Apply or revert unrelated OMV changes before installing: {", ".join(sorted(unrelated))}')


def branding_config():
    link = Element('link', rel='stylesheet', href='/interactive-vm.css')
    banner = Element('aside', {'aria-label': 'Testing virtual machine'}, id='omv-test-banner', role='note')
    SubElement(banner, 'strong').text = 'TEST VM'
    SubElement(banner, 'span').text = 'OMV Proton Drive'
    SubElement(banner, 'span').text = 'Default login: admin / admin'
    # Nginx injects the banner without modifying OMV's packaged HTML or JavaScript.
    replacements = [('</head>', link), ('</body>', banner)]
    directives: list[str] = []
    for closing, element in replacements:
        html = tostring(element, encoding='unicode', method='html')
        replacement = json.dumps(f'{html}{closing}')
        directives.append(f'sub_filter {json.dumps(closing)} {replacement};')
    return '\n'.join([*directives, ''])


def brand_web_ui(
    webroot: Path = Path('/var/www/openmediavault'), snippets: Path = Path('/etc/nginx/openmediavault-webgui.d')
) -> None:
    shutil.copyfile(Path(__file__).with_name('interactive-vm.css'), webroot / 'interactive-vm.css')
    snippets.mkdir(parents=True, exist_ok=True)
    (snippets / '90-interactive-vm.conf').write_text(branding_config())
    run('nginx', '-t')
    run('systemctl', 'reload', 'nginx')


def main(package: Path, configuration: Path | None = None):
    if not Path('/var/lib/protondrive-interactive-vm').exists():
        raise SystemExit('Run using the interactive VM harness')
    name = debian_package_name(package)
    check_pending_changes()
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    run('apt-get', 'install', '-y', '--reinstall', '--no-install-recommends', 'python3-rich', 'nftables', str(package))
    # Reproducible packages share mtimes; Salt must not reuse a previous build's
    # deployment templates after a development reinstall of the same version.
    run('salt-call', '--local', 'saltutil.clear_cache')
    run('salt-call', '--local', 'saltutil.sync_all')
    setup_traceback()
    run('systemctl', 'restart', 'openmediavault-engined')
    if configuration is not None:
        initialize(configuration)
    # Deploy Monit first: nginx/phpfpm states immediately call its control socket.
    # A partial Config.applyChanges would discard rollback history for later steps.
    run('omv-salt', 'deploy', 'run', 'monit')
    wait_for_monit()
    check_pending_changes()
    apply_configuration(*INSTALL_MODULES)
    run('omv-mkworkbench', 'all')
    brand_web_ui()
    run('dpkg', '--verify', name)
    run('systemctl', 'is-active', 'nginx', 'omv-protondrive', 'omv-protondrive-api', 'omv-protondrive-controller')
    print('PASS: real plugin installed; interactive state retained', flush=True)


if __name__ == '__main__':
    setup_traceback()
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
