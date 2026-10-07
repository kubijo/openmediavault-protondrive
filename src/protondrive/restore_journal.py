"""Typed extraction checkpoints and conservative restart reconciliation."""

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .common import BackupError

Phase = Literal['preparing', 'extracting', 'publishing', 'published']
OWNER_FILE = '.protondrive-restore-owner'


@dataclass(frozen=True)
class Publication:
    parent: str
    parent_device: int
    parent_inode: int
    staging: str
    destination: str
    device: int
    inode: int
    phase: Phase


def reconcile(value: Publication) -> bool:
    """Return True only for the exact directory whose publication was journaled."""
    from .restore import protected_directory

    with protected_directory(Path(value.parent)) as parent:
        info = os.fstat(parent)
        if (info.st_dev, info.st_ino) != (value.parent_device, value.parent_inode):
            raise BackupError('Extraction parent changed; manual recovery is required')
        root = Path(f'/proc/self/fd/{parent}')
        for name in (value.staging, value.destination):
            if Path(name).name != name or name in ('', '.', '..'):
                raise BackupError('Invalid extraction recovery path')

        def matches(name: str) -> bool:
            try:
                found = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if not stat.S_ISDIR(found.st_mode) or (found.st_dev, found.st_ino) != (value.device, value.inode):
                return False
            try:
                directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    marker_fd = os.open(OWNER_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    with os.fdopen(marker_fd, 'rb') as stream:
                        marker_info = os.fstat(stream.fileno())
                        if (
                            not stat.S_ISREG(marker_info.st_mode)
                            or marker_info.st_uid != os.geteuid()
                            or marker_info.st_mode & 0o077
                        ):
                            return False
                        marker = stream.read(256)
                finally:
                    os.close(directory)
            except OSError:
                return False
            return marker == os.fsencode(value.staging)

        if value.phase in ('publishing', 'published') and matches(value.destination):
            os.fsync(parent)
            return True
        if value.phase in ('publishing', 'published') and os.path.lexists(root / value.destination):
            raise BackupError('Published destination identity changed; manual recovery is required')
        if matches(value.staging):
            shutil.rmtree(root / value.staging)
            os.fsync(parent)
        elif os.path.lexists(root / value.staging):
            raise BackupError('Unidentified extraction staging directory retained for manual recovery')
        return False
