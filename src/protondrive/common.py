"""Shared persistence and IPC. Runtime code uses only Python's standard library."""

import contextlib
import fcntl
import json
import os
import socket
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Literal, overload

from . import records
from .json_data import JSONValue, decode, object_value
from .models import Manifest, RemoteEntry, ServiceStatus

STATE = Path('/var/lib/openmediavault-protondrive')
CONFIG = Path('/etc/openmediavault/protondrive.json')
SOCKET = Path('/run/omv-protondrive/control.sock')
BINARY = Path('/usr/lib/openmediavault-protondrive/proton-drive')
LIMIT = 4 * 1024 * 1024


class BackupError(Exception):
    """An actionable backup failure safe to present to the administrator."""


def atomic_json(path: str | Path, value: object, mode: int = 0o600) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
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
def locked(path: str | Path) -> Generator[None, None, None]:
    with open(path, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError('Another backup or recovery is running') from exc
        yield


@overload
def request(
    operation: Literal['status', 'probe', 'start-auth', 'cancel-auth', 'logout'], **params: JSONValue
) -> ServiceStatus: ...


@overload
def request(operation: Literal['prepare'], **params: JSONValue) -> list[RemoteEntry]: ...


@overload
def request(operation: Literal['upload'], **params: JSONValue) -> Manifest: ...


@overload
def request(operation: str, **params: JSONValue) -> object: ...


def request(operation: str, **params: JSONValue) -> object:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5 if operation == 'cancel-transfer' else 86500)
        client.connect(str(SOCKET))
        client.sendall(json.dumps({'operation': operation, **params}).encode() + b'\n')
        with client.makefile('rb') as stream:
            line = stream.readline(LIMIT + 1)
        if len(line) > LIMIT:
            raise BackupError('Proton service response too large')
        result = object_value(decode(line))
        if not result.get('ok'):
            raise BackupError(result.get('error', 'Proton service failed'))
        value = result['result']
        if operation in ('status', 'probe', 'start-auth', 'cancel-auth', 'logout'):
            return records.service_status(value)
        if operation == 'prepare':
            return records.listing(value)
        if operation == 'upload':
            return records.manifest(value)
        return value
