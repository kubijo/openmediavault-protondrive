"""Durable successful-run ordering, independent of status snapshots and wall clocks."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .common import BackupError, atomic_json, locked
from .json_data import JSONValue, decode, object_value


def completion_time(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise BackupError('Backup completion timestamp has no timezone')
    return timestamp


@dataclass(frozen=True)
class Completion:
    generation: int
    timestamp: str


def saved_generation(value: JSONValue) -> int | None:
    """Zero is an established baseline before the first counter-backed success."""
    if value is not None and (type(value) is not int or value < 0):
        raise BackupError('Invalid saved backup completion generation')
    return value


def read_completion(state: Path) -> Completion | None:
    path = state / 'backup-completion.json'
    if path.is_symlink():
        raise BackupError('Backup completion record must not be a symlink')
    if not path.exists():
        return None
    value = object_value(decode(path.read_text()))
    generation, timestamp = value.get('generation'), value.get('timestamp')
    if type(generation) is not int or generation < 1 or not isinstance(timestamp, str):
        raise BackupError('Invalid backup completion record')
    completion_time(timestamp)
    return Completion(generation, timestamp)


def record_completion(state: Path, timestamp: str) -> None:
    """Called only by the successful runner while it owns run.lock."""
    completion_time(timestamp)
    # Health never takes run.lock. Wait for an in-flight observation rather than
    # failing a completed backup because Monit happened to poll at this instant.
    with locked(state / 'health-backup.lock', timeout=None):
        previous = read_completion(state)
        generation = previous.generation if previous is not None else 0
        path = state / 'health-backup.json'
        if path.is_symlink():
            raise BackupError('Backup health record must not be a symlink')
        if path.exists():
            saved = object_value(decode(path.read_text()))
            baseline = saved_generation(saved.get('generation'))
            generation = max(generation, baseline or 0)
            if baseline is None:
                # Persist the old baseline FIRST. A crash here cannot manufacture
                # recovery; only the subsequent successful completion advances it.
                saved['generation'] = generation
                atomic_json(path, saved)
        atomic_json(state / 'backup-completion.json', {'generation': generation + 1, 'timestamp': timestamp})
