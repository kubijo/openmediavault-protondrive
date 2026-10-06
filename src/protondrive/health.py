"""Bounded, independent health checks consumed by OMV's Monit notifications."""

import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

from .common import STATE, BackupError, atomic_json, locked, request
from .completion import completion_time, read_completion, saved_generation
from .json_data import decode, object_value

Check = Literal['backup', 'recovery', 'auth', 'service']
CHECKS: tuple[Check, ...] = ('backup', 'recovery', 'auth', 'service')
BUSY = ('active', 'activating', 'deactivating', 'reloading')
CHECK_ERRORS = (BackupError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError)
MESSAGES: dict[Check, tuple[str, str]] = {
    'backup': (
        'Backup completed successfully.',
        'Backup failed or was interrupted. Check the Proton Drive log and retry.',
    ),
    'recovery': (
        'No container recovery is pending.',
        'Containers may still be stopped. Use Recover containers on the Proton Drive overview.',
    ),
    'auth': ('Proton Drive is signed in.', 'Proton Drive needs attention. Check the account status and sign in.'),
    'service': ('Proton Drive service is available.', 'Proton Drive service is unavailable. Check its service log.'),
}


@dataclass(frozen=True)
class Observation:
    failed: bool | None
    success: str | None = None
    generation: int | None = None


def unit_status(unit: str) -> dict[str, str]:
    output = subprocess.run(
        ['systemctl', 'show', unit, '--property=LoadState,ActiveState,Result,ExecMainStartTimestampMonotonic'],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if values.get('LoadState') != 'loaded' or not values.get('ActiveState') or not values.get('Result'):
        raise BackupError('Cannot read the Proton Drive unit state')
    return values


def observe(check: Check) -> Observation:
    """Return failure, health, or an inconclusive observation that retains the last result."""
    if check in ('service', 'auth'):
        try:
            auth = request('status', timeout=5)
        except (BackupError, OSError, ValueError, TypeError, KeyError):
            # A dead daemon is reported by the service check, not as a sign-in failure
            # or a spurious recovery of an existing authentication incident.
            return Observation(True if check == 'service' else None)
        return Observation(False if check == 'service' else auth['state'] != 'signed-in')

    unit = unit_status('omv-protondrive-backup.service')
    observation = observe_backup(check, unit)
    if observation.failed is not None and unit != unit_status('omv-protondrive-backup.service'):
        # The runner may have started between reading systemd and status.json.
        # Retry on the next poll instead of manufacturing a failure or recovery.
        return Observation(None)
    return observation


def observe_backup(check: Check, unit: dict[str, str]) -> Observation:
    running = unit['ActiveState'] in BUSY
    if check == 'recovery':
        record = STATE / 'recovery.json'
        if not (record.exists() or record.is_symlink()):
            return Observation(False)
        return Observation(None if running else True)
    if running:
        # Starting a retry is not recovery. Preserve a previously observed failure
        # until a completed backup can be observed, including across reboots.
        return Observation(None)
    path = STATE / 'status.json'
    if not path.exists():
        if unit['Result'] != 'success':
            return Observation(True, '')
        if unit.get('ExecMainStartTimestampMonotonic') == '0':
            return Observation(None)  # Fresh installation or reboot: retain any previous incident.
        raise BackupError('Backup status is missing after a run')
    status = object_value(decode(path.read_text()))
    phase = status.get('phase')
    if not isinstance(phase, str):
        raise BackupError('Backup status has no phase')
    success = status.get('lastsuccess', '')
    if not isinstance(success, str) or (phase == 'completed' and not success):
        raise BackupError('Backup status has no valid completion timestamp')
    if success:
        completion_time(success)
    completed = read_completion(STATE)
    failed = unit['Result'] != 'success' or phase != 'completed'
    if completed is not None:
        if not success or completion_time(success) != completion_time(completed.timestamp):
            # A restored status snapshot cannot stand in for the recorded latest run.
            failed = True
        return Observation(failed, completed.timestamp, completed.generation)
    return Observation(failed, success)


def newer_timestamp(candidate: str | None, baseline: str | None) -> bool:
    return (
        candidate is not None
        and bool(candidate)
        and baseline is not None
        and (not baseline or completion_time(candidate) > completion_time(baseline))
    )


def check_health(check: Check) -> int:
    """Leave delivery, duplicate suppression, retry queues and recovery alerts to Monit."""
    try:
        # Allow the completion writer to finish its atomic updates. Five seconds
        # plus at most two five-second systemctl calls fit Monit's 20-second limit.
        with locked(STATE / f'health-{check}.lock', timeout=5):
            path = STATE / f'health-{check}.json'
            previous = False
            previous_success: str | None = None
            previous_generation: int | None = None
            if path.exists():
                saved = object_value(decode(path.read_text()))
                value = saved.get('failed')
                if not isinstance(value, bool):
                    raise BackupError('Invalid saved health observation')
                previous = value
                marker = saved.get('success')
                if marker is not None and not isinstance(marker, str):
                    raise BackupError('Invalid saved backup completion timestamp')
                previous_success = marker
                if marker:
                    completion_time(marker)
                previous_generation = saved_generation(saved.get('generation'))
            incomplete = False
            try:
                observed = observe(check)
            except CHECK_ERRORS:
                observed = Observation(True)
                incomplete = True
            failed = previous if observed.failed is None else observed.failed
            success = previous_success
            if success is None or newer_timestamp(observed.success, success):
                success = observed.success if observed.success is not None else success
            generation = previous_generation
            if observed.generation is not None:
                generation = max(generation or 0, observed.generation)
            if check == 'backup' and previous and observed.failed is False:
                if previous_generation is not None:
                    failed = observed.generation is None or observed.generation <= previous_generation
                else:
                    # Legacy timestamp-only records retain their high-water mark.
                    # Unknown baselines are adopted without announcing recovery.
                    failed = not newer_timestamp(observed.success, previous_success)
            if observed.failed is not None and (
                failed != previous
                or success != previous_success
                or generation != previous_generation
                or not path.exists()
            ):
                atomic_json(path, {'failed': failed, 'success': success, 'generation': generation})
        if incomplete:
            raise BackupError('Health observation could not complete')
        message = MESSAGES[check][int(failed)]
        if observed.failed is None and not failed:
            message = 'No failure previously observed; check deferred until the operation or service is available.'
        print(message, file=sys.stderr if failed else sys.stdout)
        return int(failed)
    except CHECK_ERRORS:
        # Never turn a broken check into a recovery message or include raw account
        # responses, filesystem paths or exception diagnostics in outgoing mail.
        print(
            f'Proton Drive {check} health check could not complete. Inspect the local state and service.',
            file=sys.stderr,
        )
        return 2
