"""Require recent application backups and verify their bytes inside the archive."""

import hashlib
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, cast

from .common import BackupError
from .json_data import JSONValue
from .models import RequiredBackup


@dataclass(frozen=True)
class SelectedBackup:
    name: str
    path: Path
    pattern: str
    max_age_hours: int
    modified_ns: int
    size: int
    sha256: str

    def record(self) -> dict[str, JSONValue]:
        return {
            'kind': 'required-backup',
            'name': self.name,
            'path': str(self.path),
            'pattern': self.pattern,
            'max_age_hours': self.max_age_hours,
            'modified': datetime.fromtimestamp(self.modified_ns / 1_000_000_000, UTC).isoformat(),
            'size': self.size,
            'sha256': self.sha256,
            'capture': 'verified-file',
        }


def select(requirement: RequiredBackup, now_ns: int | None = None) -> SelectedBackup:
    now_ns = time.time_ns() if now_ns is None else now_ns
    directory = Path(requirement['directory'])
    if directory.is_symlink() or not directory.is_dir():
        raise BackupError(f'Required backup directory is missing or unsafe: {directory}')
    expression = re.compile(re.escape(requirement['pattern']).replace(r'\*', '.*'))
    candidates: list[tuple[int, Path]] = []
    try:
        for path in directory.iterdir():
            if expression.fullmatch(path.name) is None:
                continue
            details = path.lstat()
            if stat.S_ISREG(details.st_mode) and details.st_size > 0:
                candidates.append((details.st_mtime_ns, path))
    except OSError as exc:
        raise BackupError(f'Cannot inspect required backup directory: {directory}') from exc
    if not candidates:
        raise BackupError(f'No backup file matches {requirement["name"]}: {requirement["pattern"]}')
    modified_ns, path = max(candidates)
    age_ns = now_ns - modified_ns
    if age_ns < -60_000_000_000 or age_ns > requirement['max_age_hours'] * 3_600_000_000_000:
        raise BackupError(f'Required backup is not recent: {requirement["name"]}')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size == 0 or before.st_mtime_ns != modified_ns:
                raise BackupError(f'Required backup changed before reading: {requirement["name"]}')
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise BackupError(f'Required backup changed while reading: {requirement["name"]}')
    except OSError as exc:
        raise BackupError(f'Cannot read required backup: {requirement["name"]}') from exc
    return SelectedBackup(
        requirement['name'],
        path,
        requirement['pattern'],
        requirement['max_age_hours'],
        modified_ns,
        before.st_size,
        checksum,
    )


def verify_archived(archive: Path, selected: SelectedBackup) -> None:
    process = subprocess.Popen(
        [
            'tar',
            '--extract',
            '--zstd',
            '--to-stdout',
            '--file',
            str(archive),
            '--',
            str(selected.path).lstrip('/'),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    output = cast('IO[bytes]', process.stdout)
    checksum = hashlib.sha256()
    size = 0
    try:
        with output:
            while chunk := output.read(1024 * 1024):
                checksum.update(chunk)
                size += len(chunk)
        if process.wait() or size != selected.size or checksum.hexdigest() != selected.sha256:
            raise BackupError(f'Archived required backup differs from selected file: {selected.name}')
    except BaseException:
        process.kill()
        process.wait()
        raise
