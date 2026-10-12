"""Shared persistence and IPC. Runtime code uses only Python's standard library."""

import contextlib
import fcntl
import json
import os
import socket
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Literal, cast, overload

from . import records
from .json_data import JSONValue, decode, object_value
from .models import BackendStatus, Manifest, RemoteEntry, ServiceStatus

STATE = Path('/var/lib/openmediavault-protondrive')
CONFIG = Path('/etc/openmediavault/protondrive.json')
SOCKET = Path('/run/omv-protondrive/control.sock')
BINARY = Path('/usr/lib/openmediavault-protondrive/proton-drive')
LIMIT = 4 * 1024 * 1024


FailureCode = Literal['failed_precondition', 'sign_in_required', 'unavailable', 'busy', 'invalid_archive', 'internal']
FAILURE_CODES = frozenset(
    {'failed_precondition', 'sign_in_required', 'unavailable', 'busy', 'invalid_archive', 'internal'}
)


class BackupError(Exception):
    """An actionable backup failure safe to present to the administrator."""

    def __init__(self, message: str, *, code: FailureCode = 'failed_precondition') -> None:
        super().__init__(message)
        self.code = code


def atomic_json(path: str | Path, value: object, mode: int = 0o600, *, owner: tuple[int, int] | None = None) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            if owner is not None:
                os.fchown(stream.fileno(), *owner)
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sync_directory(path: str | Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def locked(path: str | Path, *, timeout: float | None = 0) -> Generator[None, None, None]:
    """Acquire immediately by default; None waits indefinitely, positive values bound the wait."""
    deadline = time.monotonic() + timeout if timeout is not None else None
    with open(path, 'a') as stream:
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (fcntl.LOCK_NB if deadline is not None else 0))
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic() if deadline is not None else 0
                if remaining <= 0:
                    raise BackupError('Another backup or recovery is running') from exc
                time.sleep(min(0.05, remaining))
        yield


@overload
def request(operation: Literal['backend-statuses'], *, timeout: float | None = None) -> list[BackendStatus]: ...


@overload
def request(
    operation: Literal['status', 'probe', 'start-auth', 'cancel-auth', 'logout'],
    *,
    timeout: float | None = None,
    **params: JSONValue,
) -> ServiceStatus: ...


@overload
def request(
    operation: Literal['prepare', 'browse'], *, timeout: float | None = None, **params: JSONValue
) -> list[RemoteEntry]: ...


@overload
def request(
    operation: Literal['upload', 'download-archive'], *, timeout: float | None = None, **params: JSONValue
) -> Manifest: ...


@overload
def request(operation: str, *, timeout: float | None = None, **params: JSONValue) -> object: ...


def request(operation: str, *, timeout: float | None = None, **params: JSONValue) -> object:
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(timeout if timeout is not None else (5 if operation == 'cancel-transfer' else 86500))
            client.connect(str(SOCKET))
            client.sendall(json.dumps({'operation': operation, **params}).encode() + b'\n')
            with client.makefile('rb') as stream:
                line = stream.readline(LIMIT + 1)
    except OSError as exc:
        raise BackupError('Proton service is unavailable; retry after it starts', code='unavailable') from exc
    if len(line) > LIMIT:
        raise BackupError('Proton service response too large', code='unavailable')
    result = object_value(decode(line))
    if not result.get('ok'):
        message = result.get('error')
        code = result.get('code')
        if not isinstance(message, str) or not isinstance(code, str) or code not in FAILURE_CODES:
            raise BackupError('Proton service returned an invalid error', code='internal')
        raise BackupError(message, code=cast(FailureCode, code))
    value = result['result']
    if operation in ('status', 'probe', 'start-auth', 'cancel-auth', 'logout'):
        return records.service_status(value)
    if operation == 'backend-statuses':
        return records.backend_statuses(value)
    if operation in ('prepare', 'browse'):
        return records.listing(value)
    if operation in ('upload', 'download-archive'):
        return records.manifest(value)
    return value
