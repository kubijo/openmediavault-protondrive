"""Durable container recovery, also invoked by systemd after abnormal exit."""

import json
import math
import re
import subprocess
import time

from .common import STATE, BackupError, atomic_json, sync_directory


def docker(*args):
    result = subprocess.run(['/usr/bin/docker', *args], capture_output=True, text=True, timeout=660, check=False)
    if result.returncode:
        raise BackupError(f'Docker {args[0]} failed: {result.stderr.strip()}')
    return result.stdout


class Recovery:
    def __init__(self, state=STATE, command=docker):
        self.path = state / 'recovery.json'
        self.command = command

    def restore(self):
        if not self.path.exists():
            return
        record = json.loads(self.path.read_text())
        if not isinstance(record, (dict, list)):
            raise BackupError('Invalid container recovery record')
        remaining = record if isinstance(record, list) else record.get('ids')
        if not isinstance(remaining, list) or any(not re.fullmatch(r'[a-f0-9]{64}', cid) for cid in remaining):
            raise BackupError('Invalid container recovery record; administrator recovery required')
        # Killing a Docker CLI does not cancel a stop request already accepted
        # by dockerd. Wait out that request before deciding a running container
        # is recovered, otherwise it could stop just after we clear the record.
        deadline = 0 if isinstance(record, list) else record.get('stop_deadline', 0)
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise BackupError('Invalid container recovery deadline')
        delay = deadline - time.time()
        if delay > 0:
            time.sleep(min(delay, 665))
        failures = []
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

    def stop(self, timeout):
        self.restore()
        ids = self.command('ps', '--quiet', '--no-trunc').split()
        if any(not re.fullmatch(r'[a-f0-9]{64}', cid) for cid in ids):
            raise BackupError('Invalid Docker container list')
        atomic_json(self.path, {'ids': ids, 'stop_deadline': 0})
        for cid in ids:
            atomic_json(self.path, {'ids': ids, 'stop_deadline': time.time() + timeout + 5})
            self.command('stop', '--time', str(timeout), cid)
            atomic_json(self.path, {'ids': ids, 'stop_deadline': 0})
        if any(self.command('inspect', '--format', '{{.State.Running}}', cid).strip() != 'false' for cid in ids):
            raise BackupError('A container is still running; archive cancelled')
