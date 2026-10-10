"""Install and exercise the plugin inside the disposable VM started by vm.py."""

import os
import subprocess
import sys
from pathlib import Path

if __package__:
    from .guest_support import ProtonDriveRpc, debian_package_name, run
else:
    from guest_support import ProtonDriveRpc, debian_package_name, run


def main(package: Path):
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Run using the disposable VM harness')
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    name = debian_package_name(package)
    Path('/data/test-tmp').mkdir(parents=True, exist_ok=True)
    os.environ['TMPDIR'] = '/data/test-tmp'
    # Also permits diagnosis using a stopped guest image from an earlier failed run.
    for installed_name in ('openmediavault-protondrive', name):
        installed = subprocess.run(
            ['dpkg-query', '-W', installed_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if installed.returncode == 0:
            run('apt-get', 'purge', '-y', installed_name)
    run('apt-get', 'install', '-y', '--no-install-recommends', str(package))
    run('systemctl', 'restart', 'openmediavault-engined')
    run('dpkg', '--verify', name)
    run('/usr/lib/openmediavault-protondrive/proton-drive', '--help')
    run(
        'python3',
        '-m',
        'unittest',
        'discover',
        '-s',
        '/src/tests/unit',
        '-v',
        env={
            **os.environ,
            'PYTHONPATH': '/usr/share/openmediavault-protondrive:/src:/usr/local/lib/omv-protondrive-vm',
        },
    )
    rpc = ProtonDriveRpc()
    settings = rpc.get_configuration()
    assert not settings['enable']
    sets = rpc.list_sets()
    assert {entry['name'] for entry in sets} == {'system', 'appData'}, sets
    rpc.save_configuration(settings)
    assert rpc.list_sets() == sets
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
    run('python3', '/src/tests/integration/notification_guest.py', str(package))
    print('PASS: OMV installation, RPC defaults, Salt deploy twice, units, workbench, lifecycle')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
