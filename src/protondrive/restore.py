"""Inspect untrusted tar files and extract selected entries into a new directory.

Inspection never extracts. Publication never replaces an existing path. The caller
must supply a verified, privately owned, uncompressed archive and hold its job lock.
"""

import ctypes
import errno
import io
import os
import shutil
import signal
import stat
import struct
import subprocess
import tarfile
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from .archive import check_space
from .common import BackupError
from .json_data import integer, validate
from .operation import OperationControl
from .restore_journal import OWNER_FILE, Phase, Publication

Kind = Literal['file', 'directory', 'symlink', 'hardlink', 'unsupported']
MAX_MEMBERS = 100_000
MAX_INDEX_BYTES = 16 * 1024 * 1024


class ArchiveReader(io.BufferedReader):
    def __init__(self, raw: io.RawIOBase) -> None:
        super().__init__(raw)
        self._extended_bytes = 0
        self._total_bytes = 0

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0 or size > MAX_INDEX_BYTES:
            raise BackupError('Archive metadata block exceeds the inspection size limit')
        self._total_bytes += size
        if size > 512:
            self._extended_bytes += size
        if self._extended_bytes > MAX_INDEX_BYTES or self._total_bytes > MAX_INDEX_BYTES + MAX_MEMBERS * 512:
            raise BackupError('Archive metadata blocks exceed the inspection size limit')
        return super().read(size)


@dataclass(frozen=True)
class Entry:
    name: str
    original: str
    kind: Kind
    size: int
    target: str
    issue: str = ''
    has_acl: bool = False


def relative_name(name: str) -> str:
    name = name.removeprefix('./').rstrip('/')
    if (
        not name
        or len(name.encode()) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or any(part in ('', '.', '..') for part in name.split('/'))
    ):
        raise BackupError('Archive contains an unsafe path')
    return name


def link_issue(entry: Entry, entries: dict[str, Entry]) -> str:
    if entry.kind not in ('symlink', 'hardlink'):
        return entry.issue
    pending = deque(entry.target.split('/'))
    resolved = entry.name.split('/')[:-1] if entry.kind == 'symlink' else []
    links = 0
    if not entry.target or entry.target.startswith('/'):
        return 'Absolute or empty link targets cannot be extracted'
    while pending:
        part = pending.popleft()
        if part in ('', '.'):
            continue
        if part == '..':
            if not resolved:
                return 'Link target escapes the extraction directory'
            resolved.pop()
            continue
        resolved.append(part)
        target = entries.get('/'.join(resolved))
        if target is not None and target.kind in ('symlink', 'hardlink'):
            links += 1
            if links > 40:
                return 'Link target contains a cycle or too many links'
            if not target.target or target.target.startswith('/'):
                return 'Link target traverses an absolute or empty link'
            resolved = resolved[:-1] if target.kind == 'symlink' else []
            pending.extendleft(reversed(target.target.split('/')))
    if entry.kind == 'hardlink':
        target = entries.get('/'.join(resolved))
        if target is None or target.kind != 'file':
            return 'Hard link target is not a regular archived file'
    return ''


def inspect(path: Path, control: OperationControl | None = None) -> list[Entry]:
    entries: dict[str, Entry] = {}
    index_bytes = 0
    archive_size = path.stat().st_size
    with (
        path.open('rb', buffering=0) as raw,
        ArchiveReader(raw) as reader,
        tarfile.open(fileobj=reader, mode='r:') as archive,
    ):
        for member in archive:
            if control is not None:
                control.check()
            if len(entries) >= MAX_MEMBERS:
                raise BackupError(f'Archive exceeds the {MAX_MEMBERS} entry inspection limit')
            name = relative_name(member.name)
            index_bytes += len(name.encode()) + len(member.linkname.encode()) + 128
            if index_bytes > MAX_INDEX_BYTES or len(member.linkname.encode()) > 4096:
                raise BackupError('Archive exceeds the inspection metadata size limit')
            if name in entries:
                raise BackupError(f'Archive contains a duplicate path: {name}')
            kind: Kind = 'unsupported'
            if member.isfile():
                kind = 'file'
            elif member.isdir():
                kind = 'directory'
            elif member.issym():
                kind = 'symlink'
            elif member.islnk():
                kind = 'hardlink'
            # Typeshed describes this implementation field as bytes; CPython
            # actually exposes (offset, length) pairs. Validate that boundary.
            sparse = validate(member.sparse)
            stored_size = member.size
            if sparse is not None:
                if not isinstance(sparse, list):
                    raise BackupError('Invalid sparse archive metadata')
                stored_size = 0
                for extent in sparse:
                    if not isinstance(extent, list) or len(extent) != 2:
                        raise BackupError('Invalid sparse archive extent')
                    offset, length = integer(extent[0]), integer(extent[1])
                    if offset < 0 or length < 0 or offset + length > member.size:
                        raise BackupError('Sparse archive extent exceeds the file size')
                    stored_size += length
            if member.size < 0 or member.offset_data + stored_size > archive_size:
                raise BackupError('Archive contains truncated or invalid file data')
            entries[name] = Entry(
                name,
                member.name,
                kind,
                member.size,
                member.linkname,
                'Device nodes, sockets and FIFOs cannot be extracted' if kind == 'unsupported' else '',
                any(key.startswith('SCHILY.acl.') for key in member.pax_headers),
            )
    for entry in entries.values():
        for parent in Path(entry.name).parents:
            ancestor = entries.get(str(parent))
            if ancestor is not None and ancestor.kind != 'directory':
                raise BackupError(f'Archive path traverses a non-directory: {entry.name}')
    return [
        Entry(item.name, item.original, item.kind, item.size, item.target, link_issue(item, entries), item.has_acl)
        for item in entries.values()
    ]


def select(entries: Sequence[Entry], names: Sequence[str]) -> list[Entry]:
    by_name = {entry.name: entry for entry in entries}
    if not names or len(names) != len(set(names)) or any(name not in by_name for name in names):
        raise BackupError('Select distinct existing archive entries')
    directories = [name + '/' for name in names if by_name[name].kind == 'directory']
    selected = {
        entry.name
        for entry in entries
        if entry.name in names or any(entry.name.startswith(prefix) for prefix in directories)
    }
    # Include directory metadata and hard-link targets, rather than letting tar
    # silently lose metadata or dereference a file outside the selection.
    pending = list(selected)
    while pending:
        name = pending.pop()
        if name == OWNER_FILE or name.startswith(OWNER_FILE + '/'):
            raise BackupError('Selection conflicts with the extraction ownership marker')
        entry = by_name[name]
        if entry.issue:
            raise BackupError(f'{name}: {entry.issue}')
        dependencies = [str(parent) for parent in Path(name).parents if str(parent) in by_name]
        if entry.kind == 'hardlink':
            dependencies.append(relative_name(entry.target))
        for dependency in dependencies:
            if dependency not in by_name:
                raise BackupError(f'Missing hard-link target: {dependency}')
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [entry for entry in entries if entry.name in selected]


@contextmanager
def protected_directory(path: Path) -> Generator[int, None, None]:
    """Open every ancestor without following links, retaining a stable parent fd."""
    if not path.is_absolute() or '..' in path.parts:
        raise BackupError('Extraction requires an absolute destination without parent traversal')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    trusted_owners = {os.fstat(fd).st_uid, os.geteuid()}
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            if info.st_uid not in trusted_owners or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
                raise BackupError('Destination ancestors must be administrator-owned and protected from renaming')
        yield fd
    finally:
        os.close(fd)


class RenameNoReplace(Protocol):
    def __call__(self, old_fd: int, old: bytes, new_fd: int, new: bytes, flags: int) -> int: ...


def publish(parent: int, staging: str, name: str) -> None:
    # Linux renameat2(RENAME_NOREPLACE) is atomic even if a conflicting entry
    # appears after preflight. No copy/delete fallback is permitted.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = cast(RenameNoReplace, cast(object, libc.renameat2))
    if rename(parent, os.fsencode(staging), parent, os.fsencode(name), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise BackupError('Extraction destination already exists')
        raise OSError(code, os.strerror(code))
    os.fsync(parent)


def supports_acl(directory: Path) -> bool:
    # Linux UAPI posix_acl_xattr.h / posix_acl.h: set the private directory's
    # existing 0700 mode as a base ACL to test whether ACL writes are supported.
    value = struct.pack('<I', 2) + b''.join(
        struct.pack('<HHI', tag, permissions, 0xFFFFFFFF) for tag, permissions in ((0x01, 7), (0x04, 0), (0x20, 0))
    )
    try:
        os.setxattr(directory, 'system.posix_acl_access', value, follow_symlinks=False)
    except OSError as error:
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return False
        raise
    return True


def extract(
    archive: Path,
    names: Sequence[str],
    destination: Path,
    reserve: int,
    progress: Callable[[str], None],
    *,
    control: OperationControl | None = None,
    checkpoint: Callable[[Publication], None] | None = None,
) -> None:
    control = control or OperationControl()
    selected = select(inspect(archive, control), names)
    if destination.name in ('', '.', '..'):
        raise BackupError('A new extraction directory is required')
    with protected_directory(destination.parent) as parent:
        stable_parent = Path(f'/proc/self/fd/{parent}')
        if os.path.lexists(stable_parent / destination.name):
            raise BackupError('Extraction destination already exists')
        check_space(stable_parent, sum(item.size + 4096 for item in selected), reserve)
        parent_info = os.fstat(parent)
        staging = stable_parent / f'.protondrive-extract-{uuid.uuid4().hex}'

        def record(phase: Phase) -> None:
            info = staging.stat() if phase != 'preparing' and phase != 'published' else None
            if phase == 'published':
                info = (stable_parent / destination.name).stat()
            if checkpoint is not None:
                checkpoint(
                    Publication(
                        str(destination.parent),
                        parent_info.st_dev,
                        parent_info.st_ino,
                        staging.name,
                        destination.name,
                        info.st_dev if info else 0,
                        info.st_ino if info else 0,
                        phase,
                    )
                )

        control.check()
        record('preparing')
        staging.mkdir(mode=0o700)
        try:
            acl = supports_acl(staging)
            if not acl and any(entry.has_acl for entry in selected):
                raise BackupError('Destination filesystem cannot preserve the selected POSIX ACLs')
            with (staging / OWNER_FILE).open('xb') as marker:
                os.fchmod(marker.fileno(), 0o600)
                marker.write(os.fsencode(staging.name))
                marker.flush()
                os.fsync(marker.fileno())
            fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            record('extracting')
            progress('Extracting selected files into a private directory')
            with tempfile.TemporaryFile() as selection, tempfile.TemporaryFile() as errors:
                selection.write(b''.join(os.fsencode(item.original) + b'\0' for item in selected))
                selection.seek(0)
                proc = subprocess.Popen(
                    [
                        'tar',
                        '--extract',
                        '--file',
                        str(archive),
                        '--directory',
                        str(staging),
                        '--numeric-owner',
                        '--same-owner',
                        '--same-permissions',
                        '--acls' if acl else '--no-acls',
                        '--xattrs',
                        '--xattrs-include=*',
                        '--keep-old-files',
                        '--no-recursion',
                        '--null',
                        '--verbatim-files-from',
                        '--files-from',
                        '-',
                    ],
                    stdin=selection,
                    pass_fds=(parent,),
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                )
                deadline = time.monotonic() + 3600
                try:
                    while proc.poll() is None:
                        control.check()
                        check_space(staging, 0, reserve)
                        if time.monotonic() >= deadline:
                            raise BackupError('Archive extraction timed out')
                        time.sleep(0.1)
                    if proc.returncode:
                        raise BackupError(f'Archive extraction failed (tar exit {proc.returncode})')
                    if os.fstat(errors.fileno()).st_size:
                        errors.seek(0)
                        detail = errors.read(4096).decode(errors='replace').strip()
                        raise BackupError(
                            f'Archive extraction reported warnings; metadata preservation could not be confirmed: {detail}'
                        )
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
            staging.chmod(0o700)
            sync_tree(staging, control)
            progress('Publishing extracted files')

            def commit() -> None:
                record('publishing')
                publish(parent, staging.name, destination.name)
                record('published')

            control.commit(commit)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def sync_tree(directory: Path, control: OperationControl) -> None:
    def failed(error: OSError) -> None:
        raise error

    for root, _, files in os.walk(directory, followlinks=False, topdown=False, onerror=failed):
        control.check()
        for name in files:
            path = Path(root) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
