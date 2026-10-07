"""Durable container recovery, also invoked by systemd after abnormal exit."""

import math
import re
import subprocess
import time
from pathlib import Path
from typing import Protocol

from .common import STATE, BackupError, atomic_json, sync_directory
from .json_data import decode


class DockerCommand(Protocol):
    def __call__(self, *args: str) -> str: ...


def docker(*args: str) -> str:
    result = subprocess.run(['/usr/bin/docker', *args], capture_output=True, text=True, timeout=660, check=False)
    if result.returncode:
        raise BackupError(f'Docker {args[0]} failed: {result.stderr.strip()}')
    return result.stdout


class Recovery:
    def __init__(self, state: Path = STATE, command: DockerCommand = docker) -> None:
        self.path = state / 'recovery.json'
        self.command = command

    def restore(self) -> None:
        if self.path.is_symlink():
            raise BackupError('Container recovery record must not be a symlink; administrator recovery required')
        if not self.path.exists():
            return
        record = decode(self.path.read_text())
        if not isinstance(record, (dict, list)):
            raise BackupError('Invalid container recovery record')
        values = record if isinstance(record, list) else record.get('ids')
        if not isinstance(values, list):
            raise BackupError('Invalid container recovery record; administrator recovery required')
        remaining: list[str] = []
        for cid in values:
            if not isinstance(cid, str) or not re.fullmatch(r'[a-f0-9]{64}', cid):
                raise BackupError('Invalid container recovery record; administrator recovery required')
            remaining.append(cid)
        # Killing a Docker CLI does not cancel a stop request already accepted
        # by dockerd. Wait out that request before deciding a running container
        # is recovered, otherwise it could stop just after we clear the record.
        deadline = 0 if isinstance(record, list) else record.get('stop_deadline', 0)
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise BackupError('Invalid container recovery deadline')
        delay = deadline - time.time()
        if delay > 0:
            time.sleep(min(delay, 665))
        failures: list[str] = []
        for cid in list(remaining):
            try:
                if self.command('inspect', '--format', '{{.State.Running}}', cid).strip() != 'true':
                    self.command('start', cid)
                if self.command('inspect', '--format', '{{.State.Running}}', cid).strip() != 'true':
                    raise BackupError('Container did not remain running')
                remaining.remove(cid)
                atomic_json(self.path, {'ids': remaining, 'stop_deadline': 0})
            except (BackupError, subprocess.TimeoutExpired) as exc:
                failures.append(f'{cid}: {exc}')
        if failures:
            raise BackupError('Container recovery incomplete: ' + '; '.join(failures))
        self.path.unlink()
        sync_directory(self.path.parent)

    def stop(self, timeout: int, selected: tuple[str, ...] | None = None) -> None:
        self.restore()
        ids = self.command('ps', '--quiet', '--no-trunc').split()
        if any(not re.fullmatch(r'[a-f0-9]{64}', cid) for cid in ids):
            raise BackupError('Invalid Docker container list')
        if selected is not None:
            if any(not re.fullmatch(r'[a-f0-9]{64}', cid) for cid in selected):
                raise BackupError('Invalid selected Docker container')
            ids = [cid for cid in ids if cid in selected]
        atomic_json(self.path, {'ids': ids, 'stop_deadline': 0})
        for cid in ids:
            atomic_json(self.path, {'ids': ids, 'stop_deadline': time.time() + timeout + 5})
            self.command('stop', '--time', str(timeout), cid)
            atomic_json(self.path, {'ids': ids, 'stop_deadline': 0})
        if any(self.command('inspect', '--format', '{{.State.Running}}', cid).strip() != 'false' for cid in ids):
            raise BackupError('A container is still running; archive cancelled')
