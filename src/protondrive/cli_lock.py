"""Inspect and preserve a malformed vendor event lock while the CLI is idle."""

import os
import stat
import uuid
from pathlib import Path

from .common import STATE, BackupError
from .json_data import decode

DIRECTORY = STATE / 'proton/.local/share/proton-drive-cli'
NAME = 'events.lock'
LIMIT = 4096
MESSAGE = 'Proton CLI event lock is corrupt. Run omv-protondrive repair-cli-lock as administrator, then retry.'


def read_lock(directory: int) -> tuple[bytes, os.stat_result] | None:
    try:
        descriptor = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise BackupError('Proton CLI event lock is not an owned regular file; inspect it manually')
        data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise BackupError('Proton CLI event lock is oversized; inspect it manually')
        return data, info


def malformed(data: bytes) -> bool:
    try:
        decode(data)
    except (ValueError, UnicodeError):
        return True
    return False


def inspect_lock(*, repair: bool = False, path: Path = DIRECTORY) -> str | None:
    """Return corruption/evidence information; caller must exclude CLI processes."""
    if path.resolve() != path.absolute():
        raise BackupError('Proton CLI state directory contains a symlink; inspect it manually')
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise BackupError('Proton CLI state directory has unsafe ownership or permissions')
        record = read_lock(directory)
        if record is None or not malformed(record[0]):
            return None
        if not repair:
            return MESSAGE
        data, before = record
        saved = f'{NAME}.corrupt-{uuid.uuid4()}'
        # Sync evidence before removing the original.
        descriptor = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
        current = read_lock(directory)
        if (
            current is None
            or current[0] != data
            or (current[1].st_dev, current[1].st_ino)
            != (
                before.st_dev,
                before.st_ino,
            )
        ):
            raise BackupError('Proton CLI event lock changed; evidence retained and repair refused')
        os.unlink(NAME, dir_fd=directory)
        os.fsync(directory)
        return str(path / saved)
    finally:
        os.close(directory)
