#!/usr/bin/env python3
"""Filesystem-backed Proton protocol fixture, used only in the disposable VM."""

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path('/var/lib/openmediavault-protondrive/proton/fake-remote')


def node(path: Path) -> dict[str, object]:
    return {
        'uid': str(path.stat().st_ino),
        'name': {'ok': True, 'value': path.name},
        'type': 'folder' if path.is_dir() else 'file',
        'ownedBy': {'email': 'regression@example.invalid'},
        'activeRevision': {'ok': True, 'value': {'claimedSize': path.stat().st_size}},
    }


def remote(name: str) -> Path:
    parts = Path(name).parts
    if len(parts) < 2 or parts[1] not in ('my-files', 'trash') or '..' in parts:
        raise ValueError('Invalid fixture path')
    return ROOT.joinpath(*parts[1:])


def main():
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Requires the disposable VM')
    for name in ('my-files', 'trash'):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    args = [arg for arg in sys.argv[1:] if arg not in ('--json', '-j')]
    if args[0] != 'filesystem':
        raise SystemExit('Unsupported fixture command')
    if (ROOT / 'force-signed-out').exists() and args[1] in ('info', 'list'):
        raise SystemExit('You need to login first')
    if (ROOT / 'hold-list').exists() and args[1] == 'list':
        (ROOT / 'hold-ready').touch()
        deadline = time.monotonic() + 20
        while not (ROOT / 'release-list').exists():
            if time.monotonic() >= deadline:
                raise SystemExit('Fixture list hold timed out')
            time.sleep(0.05)
    match args[1:]:
        case ['info', path]:
            print(json.dumps(node(remote(path))))
        case ['list', path]:
            print(json.dumps([node(child) for child in remote(path).iterdir()]))
        case ['create-folder', parent, name]:
            target = remote(parent) / name
            target.mkdir()
            print(json.dumps({'uid': str(target.stat().st_ino), 'ok': True}))
        case ['upload', '-f', 'skip', '-d', 'skip', '-t', source, parent]:
            if (ROOT / 'fail-upload').exists():
                raise SystemExit('Injected upload failure')
            target = remote(parent) / Path(source).name
            if not target.exists():
                shutil.copyfile(source, target)
        case ['download', '-f', 'skip', '-d', 'skip', source, directory]:
            shutil.copyfile(remote(source), Path(directory) / Path(source).name)
        case ['trash', path]:
            source = remote(path)
            uid = str(source.stat().st_ino)
            source.rename(ROOT / 'trash' / source.name)
            print(json.dumps({'uid': uid, 'ok': True}))
        case ['delete', path]:
            source = remote(path)
            uid = str(source.stat().st_ino)
            source.unlink()
            print(json.dumps({'uid': uid, 'ok': True}))
        case _:
            raise SystemExit('Unsupported fixture command')


if __name__ == '__main__':
    main()
