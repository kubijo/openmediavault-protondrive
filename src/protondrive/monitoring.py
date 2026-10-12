"""Confirm Monit's asynchronous control actions before completing deployment."""

import re
import subprocess
import time

from .common import BackupError
from .health import CHECKS

NAMES = {'protondrive-' + check for check in CHECKS}


def monitoring_states(output: str) -> dict[str, list[str]]:
    """Read the daemon's full status; a filtered CLI error cannot prove absence."""
    if not output.startswith('Monit '):
        raise BackupError('Invalid Monit status response')
    found: set[str] = set()
    states: dict[str, list[str]] = {}
    name: str | None = None
    for line in output.splitlines():
        if line and not line[0].isspace():
            match = re.fullmatch(r"Program '([^']+)'", line)
            name = match[1] if match and match[1] in NAMES else None
            if name is not None:
                if name in found:
                    raise BackupError('Duplicate Proton Drive check in Monit status')
                found.add(name)
        elif name is not None and (fields := line.split())[:2] == ['monitoring', 'status']:
            if name in states or fields[2:] not in (['Monitored'], ['Not', 'monitored'], ['Initializing']):
                raise BackupError('Invalid Proton Drive monitoring state')
            states[name] = fields[2:]
    if set(states) != found:
        raise BackupError('Incomplete Proton Drive monitoring status')
    return states


def set_monitoring(enabled: bool) -> None:
    action = 'monitor' if enabled else 'unmonitor'
    expected = ['Monitored'] if enabled else ['Not', 'monitored']
    deadline = time.monotonic() + 90
    while True:
        subprocess.run(['monit', '-g', 'protondrive', action], check=False, capture_output=True, timeout=5)
        result = subprocess.run(
            ['monit', '-B', 'status'],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            states = monitoring_states(result.stdout)
            # Removal must also work after failed or partial registration. Absence
            # is safe only when confirmed by a successful query of the daemon.
            if (not enabled or set(states) == NAMES) and all(state == expected for state in states.values()):
                return
        if time.monotonic() >= deadline:
            raise BackupError(f'Monit did not {action} all Proton Drive checks within 90 seconds')
        time.sleep(1)
