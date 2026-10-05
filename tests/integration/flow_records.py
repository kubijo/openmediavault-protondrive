"""Validated, Python 3.11-compatible records for recoverable live VM tests."""

from typing import TypedDict

from protondrive.json_data import boolean, integer, object_value, string, validate
from protondrive.models import RemoteEntry
from protondrive.records import listing


class ArchiveResult(TypedDict):
    archive: str
    uploaded: str


class BackupStatus(TypedDict):
    phase: str
    message: str
    error: str
    lastsuccess: str
    sets: dict[str, ArchiveResult]


class FlowRecord(TypedDict):
    before: BackupStatus
    completed: BackupStatus
    containers: list[str]
    remote: dict[str, list[RemoteEntry]]


class RetryRecord(TypedDict):
    token: str
    remote: dict[str, list[RemoteEntry]]
    completed: BackupStatus
    injected: bool
    payload_identity: list[int]
    payload_sha256: str
    armed: bool
    archive: str
    network_bytes_before_fault: int
    blocked_bytes: int
    failure_observed: bool
    watcher_error: str
    pending_sha256: str
    retry_since: float
    setuuid: str


class RestoreResult(TypedDict):
    set: str
    archive: str
    bytes: int
    restored: bool


def backup_status(value: object) -> BackupStatus:
    data = object_value(value)
    sets: dict[str, ArchiveResult] = {}
    for name, raw in object_value(data.get('sets', {})).items():
        entry = object_value(raw)
        sets[name] = {'archive': string(entry['archive']), 'uploaded': string(entry.get('uploaded', ''))}
    return {
        'phase': string(data.get('phase', '')),
        'message': string(data.get('message', '')),
        'error': string(data.get('error', '')),
        'lastsuccess': string(data.get('lastsuccess', '')),
        'sets': sets,
    }


def remote(value: object) -> dict[str, list[RemoteEntry]]:
    return {key: listing(entries) for key, entries in object_value(value).items()}


def strings(value: object) -> list[str]:
    data = validate(value)
    if not isinstance(data, list):
        raise TypeError('Expected a list of strings')
    return [string(item) for item in data]


def flow_record(value: object) -> FlowRecord:
    data = object_value(value)
    return {
        'before': backup_status(data.get('before', {})),
        'completed': backup_status(data.get('completed', {})),
        'containers': strings(data['containers']),
        'remote': remote(data.get('remote', {})),
    }


def retry_record(value: object) -> RetryRecord:
    data = object_value(value)
    identity = data.get('payload_identity', [])
    if not isinstance(identity, list) or len(identity) not in (0, 2):
        raise ValueError('Invalid payload identity')
    since = data.get('retry_since', 0)
    if isinstance(since, bool) or not isinstance(since, (float, int)):
        raise TypeError('Invalid retry timestamp')
    return {
        'token': string(data['token']),
        'remote': remote(data.get('remote', {})),
        'completed': backup_status(data.get('completed', {})),
        'injected': boolean(data.get('injected', False)),
        'payload_identity': [integer(part) for part in identity],
        'payload_sha256': string(data.get('payload_sha256', '')),
        'armed': boolean(data.get('armed', False)),
        'archive': string(data.get('archive', '')),
        'network_bytes_before_fault': integer(data.get('network_bytes_before_fault', 0)),
        'blocked_bytes': integer(data.get('blocked_bytes', 0)),
        'failure_observed': boolean(data.get('failure_observed', False)),
        'watcher_error': string(data.get('watcher_error', '')),
        'pending_sha256': string(data.get('pending_sha256', '')),
        'retry_since': float(since),
        'setuuid': string(data.get('setuuid', '')),
    }
