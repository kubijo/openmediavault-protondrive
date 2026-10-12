"""Download selected backup files in the running Proton service's private session."""

import argparse
import os
import subprocess
from pathlib import Path


def service_environment(pid: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for entry in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
        if b'=' not in entry:
            continue
        key, value = entry.split(b'=', 1)
        values[os.fsdecode(key)] = os.fsdecode(value)
    if 'DBUS_SESSION_BUS_ADDRESS' not in values:
        raise RuntimeError('Proton service has no private D-Bus session')
    return values


class Arguments(argparse.Namespace):
    pid: int
    directory: Path
    remote: list[str]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--remote', action='append', required=True)
    args = parser.parse_args(namespace=Arguments())
    if not args.directory.resolve().is_relative_to('/run/omv-protondrive'):
        raise RuntimeError('Live downloads require private runtime scratch space')
    environment = service_environment(args.pid)
    for remote in args.remote:
        result = subprocess.run(
            [
                '/usr/lib/openmediavault-protondrive/proton-drive',
                'filesystem',
                'download',
                '-f',
                'skip',
                '-d',
                'skip',
                remote,
                str(args.directory),
            ],
            user='protondrive',
            group='protondrive',
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f'Proton download failed with exit {result.returncode}')


if __name__ == '__main__':
    main()
