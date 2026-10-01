"""Shared persistence and IPC. Runtime code uses only Python's standard library."""

import contextlib
import fcntl
import json
import os
import socket
import tempfile
from pathlib import Path

STATE = Path('/var/lib/openmediavault-protondrive')
CONFIG = Path('/etc/openmediavault/protondrive.json')
SOCKET = Path('/run/omv-protondrive/control.sock')
BINARY = Path('/usr/lib/openmediavault-protondrive/proton-drive')
LIMIT = 4 * 1024 * 1024


class BackupError(Exception):
    """An actionable backup failure safe to present to the administrator."""


def atomic_json(path, value, mode=0o600):
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


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def locked(path):
    with open(path, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError('Another backup or recovery is running') from exc
        yield


def request(operation, **params):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5 if operation == 'cancel-transfer' else 86500)
        client.connect(str(SOCKET))
        client.sendall(json.dumps({'operation': operation, **params}).encode() + b'\n')
        with client.makefile('rb') as stream:
            line = stream.readline(LIMIT + 1)
        if len(line) > LIMIT:
            raise BackupError('Proton service response too large')
        result = json.loads(line)
        if not result.get('ok'):
            raise BackupError(result.get('error', 'Proton service failed'))
        return result['result']
