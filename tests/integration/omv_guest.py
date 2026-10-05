"""Install and exercise the plugin inside the disposable VM started by vm.py."""

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

if __package__:
    from .guest_support import decode, items, mapping, run, string
else:
    from guest_support import decode, items, mapping, run, string


def rpc(method: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
    result = subprocess.run(
        ['omv-rpc', '-u', 'admin', 'ProtonDrive', method, json.dumps(params or {})],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f'RPC {method} failed: {result.stdout} {result.stderr}')
    return decode(result.stdout)


def main(package: Path):
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Run using the disposable VM harness')
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    Path('/data/test-tmp').mkdir(parents=True, exist_ok=True)
    os.environ['TMPDIR'] = '/data/test-tmp'
    # Also permits diagnosis using a stopped guest image from an earlier failed run.
    installed = subprocess.run(
        ['dpkg-query', '-W', 'openmediavault-protondrive'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if installed.returncode == 0:
        run('apt-get', 'purge', '-y', 'openmediavault-protondrive')
    run('apt-get', 'install', '-y', '--no-install-recommends', str(package))
    run('systemctl', 'restart', 'openmediavault-engined')
    run('dpkg', '--verify', 'openmediavault-protondrive')
    run('/usr/lib/openmediavault-protondrive/proton-drive', '--help')
    run(
        'python3',
        '-m',
        'unittest',
        'discover',
        '-s',
        '/src/tests/unit',
        '-v',
        env={**os.environ, 'PYTHONPATH': '/usr/share/openmediavault-protondrive:/src'},
    )
    settings = rpc('get')
    assert not settings['enable']
    sets = rpc('getSetList', {'start': 0, 'limit': -1, 'sortfield': 'name', 'sortdir': 'ASC'})
    assert {string(mapping(entry)['name']) for entry in items(sets['data'])} == {'system', 'appData'}, sets
    rpc('set', settings)
    assert rpc('getSetList', {'start': 0, 'limit': -1, 'sortfield': 'name', 'sortdir': 'ASC'}) == sets
    for _ in range(2):
        run('omv-salt', 'deploy', 'run', 'protondrive')
    run('systemctl', 'is-active', 'omv-protondrive')
    timer = subprocess.run(
        ['systemctl', 'is-enabled', 'omv-protondrive-backup.timer'], capture_output=True, text=True, check=False
    )
    assert timer.stdout.strip() == 'disabled'
    run(
        'systemd-analyze',
        'verify',
        '/etc/systemd/system/omv-protondrive.service',
        '/etc/systemd/system/omv-protondrive-backup.service',
        '/etc/systemd/system/omv-protondrive-backup.timer',
        '/etc/systemd/system/omv-protondrive-recover.service',
    )
    run('omv-mkworkbench', 'all')
    run('python3', '/src/tests/integration/lifecycle_guest.py')
    run('python3', '/src/tests/integration/backup_guest.py')
    run('lintian', '--allow-root', str(package))
    print('PASS: OMV installation, RPC defaults, Salt deploy twice, units, workbench, lifecycle')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
