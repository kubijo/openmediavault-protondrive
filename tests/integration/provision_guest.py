"""Prepare or seal the plugin-free OMV base inside a disposable Debian guest."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__:
    from .vm_packages import BASE, BOOTSTRAP
else:
    from vm_packages import BASE, BOOTSTRAP


def run(*args: str) -> subprocess.CompletedProcess[bytes]:
    print('RUN', args, flush=True)
    return subprocess.run(args, check=True)


def provision(dependencies: Path):
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    run('apt-get', 'update', '-qq')
    run('apt-get', 'install', '-y', '--no-install-recommends', *BOOTSTRAP)
    run('systemctl', 'enable', '--now', 'systemd-resolved.service')
    run(
        'wget',
        '-qO',
        '/usr/share/keyrings/openmediavault.asc',
        'https://packages.openmediavault.org/public/archive.key',
    )
    Path('/etc/apt/sources.list.d/openmediavault.list').write_text(
        'deb [signed-by=/usr/share/keyrings/openmediavault.asc] https://packages.openmediavault.org/public synchrony main\n'
    )
    run('apt-get', 'update', '-qq')
    run('apt-get', 'install', '-y', '--no-install-recommends', *BASE)
    run('apt-get', 'satisfy', '-y', '--no-install-recommends', dependencies.read_text().strip())
    run('docker', '--version')
    install_seed_identity()
    run('omv-confdbadm', 'populate')
    run('dpkg', '--audit')
    print('PASS: provisioned plugin-free OMV base', flush=True)


def install_seed_identity() -> None:
    marker = Path('/etc/omv-protondrive-test-vm')
    marker.write_text('disposable VM\n')
    marker.chmod(0o600)
    run('install', '-m', '0755', '/root/vm_seed_identity.py', '/usr/local/sbin/omv-test-seed-identity')
    Path('/etc/systemd/system/omv-test-seed-identity.service').write_text(
        '[Unit]\n'
        'Description=Initialize disposable OMV test VM identity\n'
        'After=local-fs.target\n'
        'Before=ssh.service\n'
        'ConditionPathExists=/etc/omv-protondrive-test-vm\n'
        '[Service]\n'
        'Type=oneshot\n'
        'ExecStart=/usr/local/sbin/omv-test-seed-identity\n'
        'StandardOutput=journal+console\n'
        'StandardError=journal+console\n'
        'RemainAfterExit=yes\n'
    )
    drop_in = Path('/etc/systemd/system/ssh.service.d/omv-test-seed-identity.conf')
    drop_in.parent.mkdir(parents=True, exist_ok=True)
    drop_in.write_text('[Unit]\nRequires=omv-test-seed-identity.service\nAfter=omv-test-seed-identity.service\n')
    run('systemctl', 'daemon-reload')


def seal(package_name: str):
    for name in ('openmediavault-protondrive', package_name):
        installed = subprocess.run(
            ['dpkg-query', '-W', '-f=${db:Status-Status}', name],
            check=False,
            capture_output=True,
            text=True,
        )
        if installed.stdout.strip() == 'installed':
            raise RuntimeError('Refusing to cache a base containing the plugin')
    seal_identity()


def seal_identity() -> None:
    """Forget machine and SSH identity before publishing a stopped image."""
    run('systemctl', 'stop', 'docker.service', 'docker.socket', 'containerd.service')
    cloud_state = Path('/var/lib/cloud')
    if cloud_state.exists():
        shutil.rmtree(cloud_state)
    for path in Path('/var/log').glob('cloud-init*.log'):
        path.unlink()
    Path('/etc/machine-id').write_text('uninitialized\n')
    dbus_id = Path('/var/lib/dbus/machine-id')
    if not dbus_id.is_symlink():
        dbus_id.unlink(missing_ok=True)
    Path('/root/.ssh/authorized_keys').unlink(missing_ok=True)
    for key in Path('/etc/ssh').glob('ssh_host_*'):
        key.unlink()
    for name in ('provision_guest.py', 'vm_packages.py', 'vm_seed_identity.py', 'plugin-depends.txt'):
        (Path('/root') / name).unlink(missing_ok=True)
    os.sync()
    print('PASS: base sealed for fresh machine and SSH identities', flush=True)
    # Schedule within this SSH session: its key has been removed from the sealed base.
    run(
        'systemd-run',
        '--unit=protondrive-base-poweroff',
        '--on-active=3s',
        '--timer-property=AccuracySec=1s',
        '/usr/bin/systemctl',
        'poweroff',
    )


def main():
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Run using the disposable VM harness')
    if len(sys.argv) == 3 and sys.argv[1] == '--seal':
        seal(sys.argv[2])
    elif len(sys.argv) == 2:
        provision(Path(sys.argv[1]))
    else:
        raise SystemExit('Expected a dependency file or --seal PACKAGE')


if __name__ == '__main__':
    main()
