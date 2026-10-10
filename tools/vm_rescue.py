"""Preserve deleted development VM disks still held open by their QEMU process."""

import os
import signal
import stat
import subprocess
import time
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from cli_options import parse_options
from process_signals import termination_signals

FILES = ('base.qcow2', 'disk.qcow2', 'seed.iso')


def deleted_files(process: Path, instance: Path) -> dict[str, Path]:
    """Match only the exact deleted files of the requested VM instance."""
    result: dict[str, Path] = {}
    for descriptor in (process / 'fd').iterdir():
        try:
            target = str(descriptor.readlink())
        except FileNotFoundError:
            continue
        for name in FILES:
            if target == f'{instance / name} (deleted)':
                if name in result:
                    raise RuntimeError(f'Ambiguous open descriptor for {name}')
                result[name] = descriptor
    if set(result) != set(FILES):
        raise RuntimeError('QEMU does not hold all three deleted instance files')
    return result


def process_state(process: Path) -> str:
    # The comm field can contain spaces; the state follows its closing bracket.
    return (process / 'stat').read_text().rsplit(')', 1)[1].split()[0]


@contextmanager
def suspended(pidfd: int, process: Path) -> Generator[None, None, None]:
    try:
        signal.pidfd_send_signal(pidfd, signal.SIGSTOP)
        deadline = time.monotonic() + 5
        while process_state(process) not in ('T', 't'):
            if time.monotonic() >= deadline:
                raise RuntimeError('QEMU did not suspend')
            time.sleep(0.01)
        yield
    finally:
        signal.pidfd_send_signal(pidfd, signal.SIGCONT)


def capture(pid: int, state_dir: Path, output: Path) -> None:
    process = Path('/proc') / str(pid)
    instance = state_dir.resolve() / 'instance'
    if process.stat().st_uid != os.geteuid():
        raise RuntimeError('QEMU must belong to the current user')
    if 'qemu-system-x86_64' not in (process / 'exe').readlink().name:
        raise RuntimeError('The selected process is not QEMU')
    if process_state(process) in ('T', 't'):
        raise RuntimeError('QEMU is already suspended; preserve its existing suspension')
    sources = deleted_files(process, instance)
    output.mkdir(mode=0o700)
    with ExitStack() as stack:
        pidfd = os.pidfd_open(pid)
        stack.callback(os.close, pidfd)
        descriptors: dict[str, int] = {}
        for name, path in sources.items():
            fd = os.open(path, os.O_RDONLY)
            stack.callback(os.close, fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError(f'{name} is not a regular disk file')
            descriptors[name] = fd
        with suspended(pidfd, process):
            for name, fd in descriptors.items():
                os.fsync(fd)
                subprocess.run(
                    ['cp', '--reflink=auto', '--sparse=always', '--', f'/proc/self/fd/{fd}', str(output / name)],
                    pass_fds=(fd,),
                    check=True,
                )
                with (output / name).open('rb') as copied:
                    os.fsync(copied.fileno())
            directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    # The original stays alive. Never boot or repair a copy that fails this check.
    for name in ('base.qcow2', 'disk.qcow2'):
        subprocess.run(['qemu-img', 'check', str(output / name)], check=True)


@dataclass
class Options:
    """Capture deleted VM disks without terminating the original process."""

    pid: int
    state_dir: Path
    output: Path


def main() -> None:
    options = parse_options(Options)
    with termination_signals():
        capture(options.pid, options.state_dir, options.output)


if __name__ == '__main__':
    from console import install_traceback

    install_traceback()
    main()
