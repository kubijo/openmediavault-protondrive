"""Prove a cloned local owner cannot write into an existing instance folder."""

import os
import subprocess
import time
from pathlib import Path

from protondrive.json_data import decode, object_value, string

OWNER = Path('/var/lib/openmediavault-protondrive/proton/owner-id')
STATUS = Path('/var/lib/openmediavault-protondrive/status.json')
SERVICE = 'omv-protondrive.service'
FOREIGN_OWNER = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def service(action: str) -> None:
    subprocess.run(['systemctl', action, SERVICE], check=True, timeout=45)


def wait_signed_in() -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = subprocess.run(
            ['omv-protondrive', 'auth-status'], check=True, capture_output=True, text=True, timeout=10
        )
        if object_value(decode(result.stdout))['authstate'] == 'signed-in':
            return
        time.sleep(1)
    raise RuntimeError('Proton session did not become signed in')


def main() -> None:
    original_owner = OWNER.read_bytes()
    original_stat = OWNER.stat()
    if original_owner.strip().decode() == FOREIGN_OWNER:
        raise RuntimeError('Test owner identity unexpectedly matches the live installation')
    try:
        service('stop')
        OWNER.write_text(FOREIGN_OWNER + '\n')
        service('start')
        wait_signed_in()
        result = subprocess.run(
            ['omv-protondrive', 'run-now'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode == 0 or 'archiving:' in result.stdout:
            raise RuntimeError('Foreign owner was allowed to begin a backup')
        state = object_value(decode(STATUS.read_text()))
        if state.get('phase') != 'failed' or 'different installation' not in string(state.get('error', '')):
            raise RuntimeError('Foreign owner failed for an unexpected reason')
    finally:
        subprocess.run(['systemctl', 'stop', SERVICE], check=False, timeout=45)
        OWNER.write_bytes(original_owner)
        os.chown(OWNER, original_stat.st_uid, original_stat.st_gid)
        OWNER.chmod(original_stat.st_mode & 0o777)
        service('start')
    wait_signed_in()
    recovered = subprocess.run(
        ['omv-protondrive', 'run-now'],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if recovered.returncode or object_value(decode(STATUS.read_text())).get('phase') != 'completed':
        raise RuntimeError('Original owner could not complete a recovery backup')
    print('PASS: foreign owner refused before archiving; restored owner completed a new backup')


if __name__ == '__main__':
    main()
