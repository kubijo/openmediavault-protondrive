"""Verify corrupt-lock diagnosis and administrator repair in the development VM."""

import json
import os
import pwd
import subprocess
import time
from pathlib import Path

from protondrive.cli import idle
from protondrive.cli_lock import DIRECTORY, MESSAGE, NAME
from protondrive.common import STATE, BackupError, locked, request, sync_directory
from protondrive.config import load
from protondrive.json_data import decode, object_value, string

if __package__:
    from .live_ui_guest import guard
else:
    from live_ui_guest import guard

UNIT = 'omv-protondrive.service'


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60).stdout


def start() -> None:
    run('systemctl', 'start', UNIT)
    deadline = time.monotonic() + 20
    while True:
        try:
            request('status', timeout=2)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError('Proton service did not become ready') from None
            time.sleep(0.1)


def main() -> None:
    guard(load())
    if os.geteuid() != 0:
        raise RuntimeError('Requires root in the development VM')
    with locked(STATE / 'admission.lock'), locked(STATE / 'run.lock'):
        idle()
        before = request('probe')
        if before['state'] != 'signed-in':
            raise RuntimeError('Requires an already signed-in development account')
        run('systemctl', 'stop', UNIT)
        path = DIRECTORY / NAME
        injected = False
        try:
            run('runuser', '-u', 'protondrive', '--', 'mkdir', '-p', str(DIRECTORY))
            account = pwd.getpwnam('protondrive')
            with path.open('xb') as stream:
                injected = True
                os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(b'\0' * 13)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(DIRECTORY)
            start()
            try:
                request('probe')
            except BackupError as error:
                if str(error) != MESSAGE:
                    raise RuntimeError('Corrupt lock did not produce its actionable diagnostic') from error
            else:
                raise RuntimeError('Corrupt lock was not detected')
        finally:
            start()
            if injected:
                result = object_value(decode(run('omv-protondrive', 'repair-cli-lock')))
                evidence = Path(string(result['evidence']))
                if result['repaired'] is not True or evidence.parent != DIRECTORY:
                    raise RuntimeError('Repair did not report retained lock evidence')
                if evidence.read_bytes() != b'\0' * 13 or evidence.stat().st_mode & 0o777 != 0o600:
                    raise RuntimeError('Repair lost the original lock or exposed its contents')
        after = request('probe')
        if after['state'] != 'signed-in' or after['email'] != before['email']:
            raise RuntimeError('Repair did not preserve the signed-in account')
        print(json.dumps({'repaired': True, 'session_preserved': True}))


if __name__ == '__main__':
    from console import install_traceback

    install_traceback()
    main()
