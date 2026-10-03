"""Create, resume, and control a persistent local OMV VM from the user's terminal."""

import errno
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tyro
import vm_banner
import vm_runtime
from console import print_exception
from process_output import ProcessOutput
from process_signals import termination_signals
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from vm_control import VMState


@dataclass
class Options:
    action: tyro.conf.Positional[Literal['up', 'ssh', 'down', 'status', 'reset', 'install', 'snapshot', 'restore']]
    state_dir: Path = Path('.tmp/interactive-vm')
    """Persistent disk, SSH key, web password, and logs; independent of the test cache."""
    cache_dir: Path = Path('.tmp/vm-cache')
    image: Path | None = None
    package: Path | None = None
    guest_just: Path | None = None
    """Static just executable supplied by Nix for guest development commands."""
    ssh_port: int | None = None
    """Local SSH port; initially 2222, then the saved port."""
    http_port: int | None = None
    """Local OMV web port; initially 8080, then the saved port."""
    yes: bool = False
    """Acknowledge that reset deletes the local disk and Proton session."""
    force: bool = False
    """Force power-off for down; may lose unsaved guest writes."""
    no_shell: bool = False
    """Return after starting the VM instead of opening an interactive SSH shell."""
    snapshot_name: str = 'baseline'
    """Name for a stopped VM's disk snapshot or restore point."""
    no_color: bool = False
    in_clanker: bool = False


def runtime_options(options: Options, state: VMState):
    if options.image is None or options.package is None:
        raise ValueError('Use just vm::up or just vm::install to supply the Nix-built image and package')
    return vm_runtime.Options(
        image=options.image.resolve(strict=True),
        package=options.package.resolve(strict=True),
        reports=state.root / 'logs',
        cache_dir=options.cache_dir,
        no_color=options.no_color,
        in_clanker=options.in_clanker,
    )


def create(options: Options, state: VMState, output: ProcessOutput):
    if state.instance.exists():
        state.read()
        return
    runtime = runtime_options(options, state)
    runtime.reports.mkdir(parents=True, exist_ok=True)
    dependencies = vm_runtime.run(
        'dpkg-deb',
        '-f',
        str(runtime.package),
        'Depends',
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not dependencies:
        raise RuntimeError('Plugin package has no dependency metadata')
    base = vm_runtime.resolve_base(runtime, dependencies, output)
    with (
        output.stage('Create persistent interactive disk'),
        tempfile.TemporaryDirectory(prefix='.creating-', dir=state.root) as temporary,
    ):
        directory = Path(temporary)
        local_base = directory / 'base.qcow2'
        try:
            os.link(base, local_base)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            shutil.copyfile(base, local_base)
        local_base.chmod(0o444)
        # Relative backing paths survive publication and later cache removal.
        vm_runtime.prepare(directory, Path('base.qcow2'))
        password = 'admin'
        password_file = directory / 'admin-password'
        password_file.write_text(password + '\n')
        password_file.chmod(0o600)
        remote = f'/my-files/OMV Integration {uuid.uuid4().hex[:12]}'
        configuration = directory / 'interactive-init.json'
        configuration.write_text(json.dumps({'admin_password': password, 'remote_folder': remote}) + '\n')
        configuration.chmod(0o600)
        metadata = {
            'initialized': False,
            'ssh_port': options.ssh_port or 2222,
            'http_port': options.http_port or 8080,
            'remote_folder': remote,
        }
        (directory / 'instance.json').write_text(json.dumps(metadata, indent=2) + '\n')
        directory.rename(state.instance)


def connection(state: VMState):
    metadata = state.read()
    return vm_runtime.guest_connection(state.instance, state.instance / 'key', metadata['ssh_port'])


def stop(state: VMState, *, force=False):
    # OMV intentionally ignores ACPI power-button events; ask systemd directly.
    state.stop(
        force=force,
        request_shutdown=lambda: connection(state).run('systemctl', 'poweroff', '--no-block', timeout=15),
    )


def describe(state: VMState, output: ProcessOutput, status: str):
    metadata = state.read()
    console = output.console
    details = Table.grid(padding=(0, 2))
    details.add_column(style='dim')
    details.add_column()
    details.add_row('', Text(status, style='bold green' if status == 'RUNNING' else 'bold'))
    details.add_row('Web', Text(f'http://127.0.0.1:{metadata["http_port"]}/', style='bold blue'))
    details.add_row('Login', Text.assemble(('admin / admin', 'bold'), (' (default)', 'dim')))
    details.add_row('Recipes', Text('/root/justfile', style='blue'))
    details.add_row('Remote', Text(metadata['remote_folder'], style='blue'))
    details.add_row('State', Text(str(state.instance), style='blue'))
    console.print(Panel(details, title='OMV TEST VM', border_style='dim', expand=False))
    console.print()
    vm_banner.recipes(
        console,
        'Host commands · just vm::<recipe>',
        vm_runtime.SOURCE / 'tools/just/vm.just',
    )
    if state.root != Path('.tmp/interactive-vm').resolve():
        console.print('For this instance, append:')
        console.print(Text(shlex.join(['--state-dir', str(state.root)]), style='blue'))


def setup_shell(options: Options, state: VMState):
    if options.guest_just is None:
        raise ValueError('Use the Nix VM apps to supply the guest just executable')
    binary = options.guest_just.resolve(strict=True)
    guest = connection(state)
    with state.lock('shell'):
        guest.run('test', '-f', '/var/lib/protondrive-interactive-vm')
        guest.copy(
            binary,
            vm_runtime.SOURCE / 'tests/integration/guest.just',
            vm_runtime.SOURCE / 'tests/integration/guest-profile.sh',
            vm_runtime.SOURCE / 'tests/integration/guest-recipe.sh',
            vm_runtime.SOURCE / 'tests/integration/guest_commands.py',
            vm_runtime.SOURCE / 'tools/vm_banner.py',
        )
        guest.run('install', '-m', '0755', f'/root/{binary.name}', '/usr/local/bin/just')
        guest.run('install', '-m', '0644', '/root/guest.just', '/root/justfile')
        guest.run('install', '-m', '0644', '/root/guest-profile.sh', '/etc/profile.d/omv-protondrive-dev.sh')
        guest.run('touch', '/root/.hushlogin')


def open_shell(options: Options, state: VMState, output: ProcessOutput):
    if state.running() is None:
        raise RuntimeError('VM is stopped; run just vm::up first')
    setup_shell(options, state)
    describe(state, output, 'RUNNING')
    output.console.print(Text('Exiting SSH leaves the VM running.', style='dim'))
    return subprocess.run(['ssh', '-t', *connection(state).ssh[1:]], check=False).returncode


def install(options: Options, state: VMState, output: ProcessOutput):
    if options.package is None:
        raise ValueError('Use just vm::install to supply the Nix-built package')
    package = options.package.resolve(strict=True)
    with state.lock('install'):
        if state.running() is None:
            raise RuntimeError('VM is stopped; run just vm::up first')
        metadata = state.read()
        guest = connection(state)
        # A previous disconnected install may still own a remote service.
        guest.ensure_idle()
        script = vm_runtime.SOURCE / 'tests/integration/interactive_guest.py'
        with output.stage('Install plugin in interactive VM'):
            guest.copy(script, script.with_name('interactive-vm.css'), vm_runtime.SOURCE / 'tools/console.py', package)
            arguments = [f'/root/{package.name}']
            if not metadata['initialized']:
                guest.copy(state.instance / 'interactive-init.json')
                arguments.append('/root/interactive-init.json')
            guest.python(output, '/root/interactive_guest.py', state.root / 'logs/install.log', 1800, *arguments)
            metadata['initialized'] = True
            state.write(metadata)
            (state.instance / 'interactive-init.json').unlink(missing_ok=True)
        setup_shell(options, state)


def up(options: Options, state: VMState, output: ProcessOutput):
    with state.lock():
        if state.running() is not None:
            metadata = state.read()
            for key in ('ssh_port', 'http_port'):
                requested = getattr(options, key)
                if requested is not None and requested != metadata[key]:
                    raise ValueError('Stop the VM before changing its forwarded ports')
            if not metadata['initialized']:
                install(options, state, output)
            return
        create(options, state, output)
        metadata = state.read()
        metadata['ssh_port'] = options.ssh_port or metadata['ssh_port']
        metadata['http_port'] = options.http_port or metadata['http_port']
        if metadata['ssh_port'] == metadata['http_port']:
            raise ValueError('SSH and HTTP require different host ports')
        state.write(metadata)
        reports = state.root / 'logs'
        reports.mkdir(exist_ok=True)
        state.monitor.unlink(missing_ok=True)
        command = vm_runtime.qemu_command(
            state.instance,
            reports / 'serial.log',
            {22: metadata['ssh_port'], 80: metadata['http_port']},
        )
        command.extend(['-daemonize', '-qmp', f'unix:{state.monitor},server=on,wait=off'])
        with (reports / 'qemu.log').open('a') as log:
            subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        runtime = vm_runtime.Options(image=state.instance / 'base.qcow2', package=Path('.'), reports=reports)
        guest = vm_runtime.connect(
            runtime,
            state.instance,
            state.instance / 'key',
            metadata['ssh_port'],
            None,
            output,
            '',
            disposable=False,
            is_running=lambda: state.running() is not None,
        )
        guest.run('touch', '/var/lib/protondrive-interactive-vm')
        if not metadata['initialized']:
            install(options, state, output)


def reset(options: Options, state: VMState, output: ProcessOutput):
    if not options.yes:
        raise ValueError('Reset deletes the local VM disk and Proton session. Stop the VM, then use vm::reset --yes')
    with state.lock():
        if state.running() is not None:
            raise RuntimeError('Stop the VM before resetting it')
        if state.instance.exists():
            state.read()
            shutil.rmtree(state.instance)
        state.monitor.unlink(missing_ok=True)
    output.console.print(
        'VM: RESET. Next vm::up creates a fresh disk from the current base. Remote files are retained.'
    )


def snapshot(options: Options, state: VMState, output: ProcessOutput, *, restore=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', options.snapshot_name):
        raise ValueError('Snapshot names must be 1–64 letters, digits, dots, underscores, or hyphens')
    with state.lock():
        if state.running() is not None:
            raise RuntimeError('Stop the VM before creating or restoring a snapshot')
        metadata = state.read()
        if not metadata['initialized']:
            raise RuntimeError('Finish VM initialization before creating or restoring a snapshot')
        disk = state.instance / 'disk.qcow2'
        if not disk.is_file():
            raise RuntimeError(f'VM disk is missing: {disk}')
        operation = '-a' if restore else '-c'
        subprocess.run(['qemu-img', 'snapshot', operation, options.snapshot_name, str(disk)], check=True)
    output.console.print(f'VM: {"RESTORED" if restore else "SNAPSHOT"} {options.snapshot_name}')


@termination_signals()
def main(options: Options):
    output = ProcessOutput(no_color=options.no_color, in_clanker=options.in_clanker)
    state = VMState(options.state_dir.resolve())
    try:
        for port in (options.ssh_port, options.http_port):
            if port is not None and not 1024 <= port <= 65535:
                raise ValueError('Host ports must be between 1024 and 65535')
        if len(os.fsencode(state.monitor)) >= 108:
            raise ValueError('State directory is too long for a Unix socket; choose a shorter --state-dir')
        if options.action == 'up':
            up(options, state, output)
            if options.no_shell:
                describe(state, output, 'RUNNING')
            else:
                raise SystemExit(open_shell(options, state, output))
        elif options.action == 'install':
            install(options, state, output)
        elif options.action == 'ssh':
            raise SystemExit(open_shell(options, state, output))
        elif options.action == 'down':
            with output.stage('Stop interactive VM'):
                if state.running() is None:
                    # A boot in progress owns this lock before its monitor exists.
                    with state.lock():
                        stop(state, force=options.force)
                else:
                    stop(state, force=options.force)
            output.console.print('VM: STOPPED; persistent disk retained')
        elif options.action == 'reset':
            reset(options, state, output)
        elif options.action in ('snapshot', 'restore'):
            snapshot(options, state, output, restore=options.action == 'restore')
        elif state.metadata.exists():
            status = state.running()
            describe(state, output, status['status'].upper() if status else 'STOPPED')
        else:
            output.console.print('VM: NOT CREATED; run just vm::up')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print_exception(no_color=options.no_color, in_clanker=options.in_clanker)
        raise SystemExit(1) from None
    except KeyboardInterrupt as error:
        output.console.print(
            'Interrupted; the VM is not shut down. Check just vm::status; use just vm::down to shut it down.'
        )
        raise SystemExit(128 + getattr(error, 'signum', signal.SIGINT)) from None


if __name__ == '__main__':
    main(tyro.cli(Options))
