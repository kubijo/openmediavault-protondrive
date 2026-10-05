#!/usr/bin/env python3
"""Executable just stub for the unattended VM flow tests."""

import json
import os
import sys
from pathlib import Path

DEVELOPMENT_ROOT = '/my-files/open-media-vault-proton-backup-development'


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def state_dir(args: list[str]) -> Path:
    return Path(option(args, '--state-dir'))


def boot(args: list[str]) -> None:
    state = state_dir(args)
    metadata = state / 'instance/instance.json'
    metadata.parent.mkdir(parents=True, exist_ok=True)
    if not metadata.exists():
        reset = (state / 'reset-marker').exists() and not os.environ.get('SAME_RESET')
        metadata.write_text(json.dumps({'proton_instance_uuid': 'new' if reset else 'initial'}))


def reset(args: list[str]) -> None:
    state = state_dir(args)
    (state / 'instance/instance.json').unlink(missing_ok=True)
    (state / 'reset-marker').touch()


def main(args: list[str]) -> int:
    match args[0]:
        case 'vm::probe':
            print(json.dumps({'ok': True, 'remote_folder': DEVELOPMENT_ROOT, 'screenshots': []}))
        case 'vm::up':
            boot(args)
        case 'vm::reset':
            reset(args)
        case 'vm::down' | 'vm::snapshot' | 'vm::restore':
            pass
        case 'test::vm':
            return int(bool(os.environ.get('FAIL_BACKUP')))
        case _:
            raise ValueError('Unsupported fixture command')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
