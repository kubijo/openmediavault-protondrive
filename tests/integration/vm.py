"""Boot a disposable OMV 7 VM, transfer fixtures, run tests, and remove the guest."""

import os
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import tyro
import yaml


@dataclass
class Options:
    image: Path
    package: Path
    reports: Path = Path('.tmp/vm-tests')
    """Directory for serial and test logs, retained after guest cleanup."""
    keep_failed: bool = False
    """Retain the stopped disk after failure for local diagnosis; contains only fixtures."""
    timeout: int = 1800
    """Maximum guest test duration in seconds."""


def run(*args: str, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def prepare(directory: Path, image: Path):
    key = directory / 'key'
    run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
    user_data = {
        'hostname': 'protondrive-test',
        'disable_root': False,
        'ssh_pwauth': False,
        'users': [{'name': 'root', 'ssh_authorized_keys': [key.with_suffix('.pub').read_text().strip()]}],
    }
    (directory / 'user-data').write_text('#cloud-config\n' + yaml.safe_dump(user_data))
    (directory / 'meta-data').write_text(yaml.safe_dump({'instance-id': directory.name}))
    run(
        'genisoimage',
        '-quiet',
        '-output',
        str(directory / 'seed.iso'),
        '-volid',
        'cidata',
        '-joliet',
        '-rock',
        str(directory / 'user-data'),
        str(directory / 'meta-data'),
    )
    run('qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(image), str(directory / 'disk.qcow2'), '16G')
    return key


def test_guest(options: Options, directory: Path, key: Path, port: int, process):
    ssh_options = [
        '-F',
        '/dev/null',
        '-i',
        str(key),
        '-o',
        'BatchMode=yes',
        '-o',
        'StrictHostKeyChecking=accept-new',
        '-o',
        f'UserKnownHostsFile={directory / "known_hosts"}',
        '-o',
        'ConnectTimeout=5',
    ]
    ssh = ['ssh', *ssh_options, '-p', str(port), 'root@127.0.0.1']
    deadline = time.monotonic() + 180
    while subprocess.run([*ssh, 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode:
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('VM failed to become reachable; inspect serial.log')
        time.sleep(2)
    source = Path(__file__).resolve().parents[2]
    bundle = directory / 'source.tar'
    with tarfile.open(bundle, 'w') as archive:
        for name in ('src', 'tests'):
            archive.add(source / name, arcname=name)
    run('scp', *ssh_options, '-P', str(port), str(bundle), str(options.package), 'root@127.0.0.1:/root/')
    run(*ssh, shlex.join(['mkdir', '-p', '/src']))
    run(*ssh, shlex.join(['tar', '-xf', '/root/source.tar', '-C', '/src']))
    run(*ssh, shlex.join(['touch', '/run/protondrive-disposable-test']))
    with (options.reports / 'tests.log').open('w') as log:
        run(
            *ssh,
            shlex.join(['python3', '/src/tests/integration/omv_guest.py', f'/root/{options.package.name}']),
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=options.timeout,
        )


def main(options: Options):
    options.image = options.image.resolve(strict=True)
    options.package = options.package.resolve(strict=True)
    options.reports.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='protondrive-vm-') as temporary:
        directory = Path(temporary)
        key = prepare(directory, options.image)
        port = free_port()
        accelerator = 'kvm' if os.access('/dev/kvm', os.R_OK | os.W_OK) else 'tcg'
        command = [
            'qemu-system-x86_64',
            '-accel',
            accelerator,
            '-cpu',
            'host' if accelerator == 'kvm' else 'max',
            '-m',
            '3072',
            '-smp',
            '2',
            '-display',
            'none',
            '-serial',
            f'file:{options.reports.resolve() / "serial.log"}',
            '-drive',
            f'file={directory / "disk.qcow2"},format=qcow2,if=virtio',
            '-drive',
            f'file={directory / "seed.iso"},format=raw,media=cdrom,readonly=on',
            '-netdev',
            f'user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22',
            '-device',
            'virtio-net-pci,netdev=net0',
        ]
        try:
            with subprocess.Popen(command, stdin=subprocess.DEVNULL) as process:
                try:
                    test_guest(options, directory, key, port, process)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        except BaseException:
            if options.keep_failed:
                saved = options.reports / 'failed.qcow2'
                shutil.copyfile(directory / 'disk.qcow2', saved)
                print(f'Retained stopped test disk: {saved}', flush=True)
            raise
    print(f'PASS: OMV VM tests. Logs: {options.reports}')


if __name__ == '__main__':
    main(tyro.cli(Options))
