"""Own and verify only the selected-file restore probe's local VM fixtures."""

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tyro

from cli_options import parse_options
from protondrive.common import STATE, atomic_json, locked
from protondrive.config import identifier, load
from protondrive.json_data import decode, object_value

if __package__:
    from .live_ui_guest import guard
else:
    from live_ui_guest import guard

ROOT = Path('/var/lib/protondrive-owned-ui-restore')
SOURCE = Path('/data/interactive-fixtures/system/example.txt')


def owned(token: str) -> Path:
    directory = ROOT / identifier(token)
    for path in (ROOT, directory):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError('Restore fixture directory is not private and owned')
    record = object_value(decode((directory / 'owner.json').read_text()))
    if record.get('token') != token:
        raise RuntimeError('Restore fixture ownership does not match')
    return directory


def prepare(token: str) -> dict[str, str]:
    token = identifier(token)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    root_info = ROOT.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid() or root_info.st_mode & 0o077:
        raise RuntimeError('Restore fixture parent is not private and owned')
    directory = ROOT / token
    directory.mkdir(mode=0o700)
    atomic_json(directory / 'owner.json', {'token': token, 'expected': SOURCE.read_text()})
    return {'destination': str(directory / 'files')}


def verify(token: str) -> dict[str, bool]:
    directory = owned(token)
    restored = directory / 'files' / SOURCE.relative_to('/')
    for path in (restored, *restored.parents):
        if path == directory:
            break
        if path.is_symlink():
            raise RuntimeError('Unexpected symlink in selected file restoration')
    record = object_value(decode((directory / 'owner.json').read_text()))
    if restored.read_text() != record['expected']:
        raise RuntimeError('Restored fixture content differs from the source')
    if (directory / 'files/data/interactive-fixtures/appData').exists():
        raise RuntimeError('Restoration included an unselected set')
    return {'verified': True}


def cleanup(token: str) -> dict[str, bool]:
    with locked(STATE / 'run.lock'):
        directory = ROOT / identifier(token)
        if not directory.exists() and not directory.is_symlink():
            return {'cleaned': True}
        shutil.rmtree(owned(token))
    return {'cleaned': True}


@dataclass
class Arguments:
    """Own and verify only the selected-file restore probe's local VM fixtures."""

    action: tyro.conf.Positional[Literal['prepare', 'verify', 'cleanup']]
    """Fixture operation to perform."""
    token: tyro.conf.Positional[str]
    """UUID identifying this probe's owned fixtures."""


def main() -> None:
    args = parse_options(Arguments)
    guard(load())
    match args.action:
        case 'prepare':
            print(json.dumps(prepare(args.token)))
        case 'verify':
            print(json.dumps(verify(args.token)))
        case 'cleanup':
            print(json.dumps(cleanup(args.token)))


if __name__ == '__main__':
    from console import install_traceback

    install_traceback()
    main()
