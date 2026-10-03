"""Shared QEMU boot/provisioning helpers and the disposable OMV integration runner."""

import os
import shlex
import signal
import socket
import subprocess
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import tyro
import yaml
from console import child_environment, print_exception
from process_output import ProcessOutput
from process_signals import termination_signals
from rich.text import Text
from vm_cache import BaseCache, fingerprint

SOURCE = Path(__file__).resolve().parents[1]
JOB_UNIT = 'omv-protondrive-harness.service'


@dataclass
class Options:
    image: Path
    package: Path
    reports: Path = Path('.tmp/vm-tests')
    """Directory for serial and test logs, retained after guest cleanup."""
    cache_dir: Path = Path('.tmp/vm-cache')
    """Reusable plugin-free OMV bases; every test still gets a fresh overlay."""
    no_cache: bool = False
    """Provision from the Debian image without reading or writing the base cache."""
    refresh_base: bool = False
    """Build and publish a new base even when matching inputs are cached."""
    keep_failed: bool = False
    """Retain the stopped disk after failure for local diagnosis; contains only fixtures."""
    timeout: int = 1800
    """Maximum guest test duration in seconds."""
    no_color: bool = False
    """Disable terminal colours and animations."""
    in_clanker: bool = False
    """Use plain output without animations."""


def run(*args: str, check=True, **kwargs):
    return subprocess.run(args, check=check, **kwargs)


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
    run(
        'qemu-img',
        'create',
        '-f',
        'qcow2',
        '-F',
        'qcow2',
        '-b',
        str(image),
        str(directory / 'disk.qcow2'),
        '16G',
        cwd=directory,
    )
    return key


@dataclass
class Guest:
    ssh: list[str]
    scp: list[str]
    directory: Path
    process: subprocess.Popen | None

    def run(self, *args, **kwargs):
        return run(*self.ssh, shlex.join(args), **kwargs)

    def copy(self, *paths):
        run(*self.scp, *map(str, paths), 'root@127.0.0.1:/root/')

    @property
    def job_marker(self):
        return self.directory / 'pending-job'

    def cancel_job(self):
        """Stop the whole remote service and acknowledge cancellation before retrying."""
        self.run('systemctl', 'stop', JOB_UNIT, check=False, timeout=30)
        result = self.run(
            'systemctl',
            'show',
            JOB_UNIT,
            '--property=ActiveState',
            '--value',
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.stdout.strip() not in ('inactive', 'failed'):
            raise RuntimeError('Remote provisioning has not stopped; retry after checking the VM')
        self.job_marker.unlink(missing_ok=True)

    def ensure_idle(self):
        if self.job_marker.exists():
            self.cancel_job()

    def python(self, output: ProcessOutput, script: str, log: Path, timeout: int, *args):
        self.ensure_idle()
        environment = child_environment(terminal=output.animate)
        variables = [
            f'{key}={environment[key]}'
            for key in ('TERM', 'NO_COLOR', 'FORCE_COLOR', 'CLICOLOR_FORCE', 'PYTHON_COLORS')
            if key in environment
        ]
        remote = shlex.join(
            [
                'systemd-run',
                '--quiet',
                '--wait',
                '--pipe',
                '--collect',
                '--service-type=exec',
                f'--unit={JOB_UNIT}',
                '--property=KillMode=control-group',
                '--property=TimeoutStopSec=15s',
                f'--property=RuntimeMaxSec={timeout}s',
                'env',
                *variables,
                'python3',
                '-u',
                script,
                *args,
            ]
        )
        # Allocate a remote output terminal without exposing interactive stdin.
        command = [self.ssh[0], '-tt' if output.animate else '-T', *self.ssh[1:], f'{remote} < /dev/null']
        # A stable unit name also prevents overlapping jobs if the connection is lost.
        self.job_marker.write_text(JOB_UNIT + '\n')
        try:
            output.run(command, log, timeout=timeout)
        except BaseException:
            # Keep the marker when cancellation cannot be confirmed. The next install
            # must reconcile it before copying over files a surviving job could use.
            self.cancel_job()
            raise
        else:
            self.job_marker.unlink(missing_ok=True)


def guest_connection(directory: Path, key: Path, port: int, process=None):
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
    return Guest(
        ssh=['ssh', *ssh_options, '-p', str(port), 'root@127.0.0.1'],
        scp=['scp', '-q', *ssh_options, '-P', str(port)],
        directory=directory,
        process=process,
    )


def connect(
    options: Options,
    directory: Path,
    key: Path,
    port: int,
    process,
    output: ProcessOutput,
    label: str,
    *,
    disposable=True,
    is_running=None,
):
    guest = guest_connection(directory, key, port, process)
    with output.stage('Boot VM and wait for SSH'):
        deadline = time.monotonic() + 180
        while subprocess.run(
            [*guest.ssh, 'true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        ).returncode:
            alive = is_running() if is_running is not None else process.poll() is None
            if not alive or time.monotonic() > deadline:
                raise RuntimeError(f'VM failed to become reachable; inspect {label}serial.log')
            time.sleep(2)
        output.run(
            [*guest.ssh, 'cloud-init status --wait'],
            options.reports / f'{label}cloud-init.log',
            timeout=180,
        )
    if disposable:
        guest.run('touch', '/run/protondrive-disposable-test')
    return guest


def qemu_command(directory: Path, serial: Path, ports: dict[int, int]):
    accelerator = 'kvm' if os.access('/dev/kvm', os.R_OK | os.W_OK) else 'tcg'
    forwards = [f'hostfwd=tcp:127.0.0.1:{host}-:{guest}' for guest, host in ports.items()]
    return [
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
        f'file:{serial}',
        '-drive',
        f'file={directory / "disk.qcow2"},format=qcow2,if=virtio',
        '-drive',
        f'file={directory / "seed.iso"},format=raw,media=cdrom,readonly=on',
        '-netdev',
        ','.join(['user', 'id=net0', *forwards]),
        '-device',
        'virtio-net-pci,netdev=net0',
    ]


def provision(guest: Guest, options: Options, dependencies: str, output: ProcessOutput):
    with output.stage('Provision plugin-free OMV base'):
        dependency_file = guest.directory / 'plugin-depends.txt'
        dependency_file.write_text(dependencies)
        guest.copy(SOURCE / 'tests/integration/provision_guest.py', dependency_file)
        guest.python(
            output,
            '/root/provision_guest.py',
            options.reports / 'base-provision.log',
            options.timeout,
            '/root/plugin-depends.txt',
        )


def test_guest(guest: Guest, options: Options, output: ProcessOutput):
    bundle = guest.directory / 'source.tar'
    with output.stage('Transfer package and fixtures'):
        with tarfile.open(bundle, 'w') as archive:
            for name in ('src', 'tests'):
                archive.add(SOURCE / name, arcname=name)
        guest.copy(bundle, options.package)
        guest.run('mkdir', '-p', '/src')
        guest.run('tar', '-xf', '/root/source.tar', '-C', '/src')
    with output.stage('Install current plugin and run guest tests'):
        guest.python(
            output,
            '/src/tests/integration/omv_guest.py',
            options.reports / 'tests.log',
            options.timeout,
            f'/root/{options.package.name}',
        )


@contextmanager
def boot(options: Options, image: Path, output: ProcessOutput, *, label=''):
    with tempfile.TemporaryDirectory(prefix='protondrive-vm-') as temporary:
        directory = Path(temporary)
        with output.stage('Prepare disposable VM'):
            key = prepare(directory, image)
        port = free_port()
        accelerator = 'kvm' if os.access('/dev/kvm', os.R_OK | os.W_OK) else 'tcg'
        output.console.print(f'Acceleration: {accelerator}; logs: {options.reports.resolve()}')
        command = qemu_command(directory, options.reports.resolve() / (label + 'serial.log'), {22: port})
        try:
            with subprocess.Popen(command, stdin=subprocess.DEVNULL) as process:
                try:
                    yield connect(options, directory, key, port, process, output, label)
                finally:
                    with output.stage('Stop disposable VM'):
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5)
        except BaseException:
            if options.keep_failed:
                saved = options.reports / 'failed.qcow2'
                run('qemu-img', 'convert', '-O', 'qcow2', str(directory / 'disk.qcow2'), str(saved))
                output.console.print(f'Retained stopped test disk: {saved}')
            raise


def base_inputs(options: Options, dependencies: str):
    return {
        'image': fingerprint(options.image),
        'provision': fingerprint(SOURCE / 'tests/integration/provision_guest.py'),
        'harness': fingerprint(Path(__file__)),
        'cache': fingerprint(SOURCE / 'tools/vm_cache.py'),
        'dependencies': dependencies,
        'qemu': run('qemu-system-x86_64', '--version', capture_output=True, text=True).stdout,
    }


def build_base(destination: Path, options: Options, dependencies: str, output: ProcessOutput):
    with boot(options, options.image, output, label='base-') as guest:
        provision(guest, options, dependencies, output)
        with output.stage('Seal and shut down reusable base'):
            guest.python(output, '/root/provision_guest.py', options.reports / 'base-seal.log', 120, '--seal')
            if guest.process.wait(timeout=120) != 0:
                raise RuntimeError('Base VM did not shut down cleanly; cache will not be published')
            run('qemu-img', 'convert', '-O', 'qcow2', str(guest.directory / 'disk.qcow2'), str(destination))
            run('qemu-img', 'check', str(destination))


def run_vm(options: Options, output: ProcessOutput):
    if options.no_cache and options.refresh_base:
        raise ValueError('--no-cache and --refresh-base cannot be combined')
    options.image = options.image.resolve(strict=True)
    options.package = options.package.resolve(strict=True)
    options.reports.mkdir(parents=True, exist_ok=True)
    dependencies = run('dpkg-deb', '-f', str(options.package), 'Depends', capture_output=True, text=True).stdout.strip()
    if not dependencies:
        raise RuntimeError('Plugin package has no dependency metadata')
    image = options.image
    if options.no_cache:
        output.console.print('CACHE BYPASS: provisioning a clean Debian image')
    else:
        image = resolve_base(options, dependencies, output)
    with boot(options, image, output) as guest:
        if options.no_cache:
            provision(guest, options, dependencies, output)
        test_guest(guest, options, output)


def resolve_base(options: Options, dependencies: str, output: ProcessOutput):
    with output.stage('Resolve reusable OMV base'):
        cache = BaseCache(options.cache_dir, base_inputs(options, dependencies))

        def build(destination):
            output.console.print('CACHE REFRESH' if options.refresh_base else 'CACHE MISS: building OMV base')
            build_base(destination, options, dependencies, output)

        image, built = cache.ensure(build, refresh=options.refresh_base)
        output.console.print(f'CACHE {"BUILT" if built else "HIT"}: {image}')
        return image


@termination_signals()
def main(options: Options):
    output = ProcessOutput(no_color=options.no_color, in_clanker=options.in_clanker)
    output.console.print(Text('OMV VM integration tests', style='bold'))
    output.console.print('The disposable VM is removed when this test finishes.')
    started = time.monotonic()
    try:
        run_vm(options, output)
    except KeyboardInterrupt as error:
        output.console.print(Text(f'CANCELLED: OMV VM tests. Logs: {options.reports}', style='bold yellow'))
        raise SystemExit(128 + getattr(error, 'signum', signal.SIGINT)) from None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print_exception(no_color=options.no_color, in_clanker=options.in_clanker)
        output.console.print(Text(f'FAILED: OMV VM tests. Logs: {options.reports}', style='bold red'))
        raise SystemExit(1) from None
    output.console.print(
        Text(f'PASS: OMV VM tests ({time.monotonic() - started:.1f}s). Logs: {options.reports}', style='bold green')
    )


if __name__ == '__main__':
    main(tyro.cli(Options))
