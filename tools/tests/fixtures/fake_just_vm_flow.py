#!/usr/bin/env python3
"""Executable VM/probe stubs for regression-runner process tests; never boots a VM."""

import json
import os
import signal
import sys
import time
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
    (state / 'running').touch()
    if not metadata.exists():
        reset = (state / 'reset-marker').exists() and not os.environ.get('SAME_RESET')
        metadata.write_text(json.dumps({'proton_instance_uuid': 'new' if reset else 'initial'}))
    if os.environ.get('FAIL_INIT'):
        raise SystemExit(1)
    if os.environ.get('INTERRUPT_INIT'):
        os.kill(os.getppid(), signal.SIGTERM)
        time.sleep(30)
    if os.environ.get('TIMEOUT_INIT'):
        time.sleep(30)


def reset(args: list[str]) -> None:
    state = state_dir(args)
    if (state / 'running').exists():
        raise RuntimeError('Attempt to delete a running VM')
    (state / 'instance/instance.json').unlink(missing_ok=True)
    (state / 'reset-marker').touch()


def main(name: str, args: list[str]) -> int:
    match name:
        case 'web-probe':
            if os.environ.get('BAD_PROBE'):
                print('{"ok": false}')
            else:
                print(json.dumps({'ok': True, 'remote_folder': DEVELOPMENT_ROOT, 'screenshots': []}))
        case 'vm-up':
            boot(args)
        case 'vm-control':
            if args[0] == 'reset':
                reset(args)
            elif args[0] == 'down':
                marker = state_dir(args) / 'teardown-interrupted'
                if os.environ.get('INTERRUPT_TEARDOWN') and not marker.exists():
                    marker.touch()
                    os.kill(os.getppid(), signal.SIGTERM)
                    time.sleep(30)
                if os.environ.get('FAIL_STOP') and '--force' not in args:
                    return 1
                if os.environ.get('FAIL_FORCE_STOP'):
                    return 1
                (state_dir(args) / 'running').unlink(missing_ok=True)
        case 'vm-command':
            pass
        case 'test-vm':
            return int(bool(os.environ.get('FAIL_BACKUP')))
        case _:
            raise ValueError(f'Unsupported fixture command: {name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[0]).name, sys.argv[1:]))
