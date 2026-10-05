"""Metadata-preserving archives with bounded staging and atomic publication."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .common import BackupError, atomic_json, sync_directory
from .config import lines
from .models import BackupSet, Manifest


def excluded(path: Path, sources: list[Path], excludes: list[str]) -> bool:
    return any(path == source / value or source / value in path.parents for source in sources for value in excludes)


def estimate(item: BackupSet) -> int:
    sources = [Path(p) for p in lines(item['paths'])]
    excludes = lines(item['excludes'])
    size = 1024 * 1024
    seen: set[tuple[int, int]] = set()
    for source in sources:
        if not source.exists() and not source.is_symlink():
            raise BackupError(f'Source does not exist: {source}')
        candidates = [source]
        if source.is_dir() and not source.is_symlink():

            def onerror(error: OSError) -> None:
                raise error

            for root, dirs, files in os.walk(source, onerror=onerror, followlinks=False):
                dirs[:] = [d for d in dirs if not excluded(Path(root) / d, sources, excludes)]
                candidates.extend(Path(root) / name for name in dirs + files)
        for path in candidates:
            if excluded(path, sources, excludes):
                continue
            st = path.lstat()
            inode = (st.st_dev, st.st_ino)
            if inode not in seen:
                seen.add(inode)
                size += st.st_size + 4096
    return size + size // 100


def check_space(directory: str | Path, required: int, reserve: int) -> None:
    if shutil.disk_usage(directory).free < required + reserve:
        raise BackupError(f'Insufficient staging space: need {required + reserve} free bytes')


def archive(item: BackupSet, partial: Path, reserve: int) -> None:
    """Return only after tar and its compressor have exited; kill both on failure."""
    sources = lines(item['paths'])
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    args = [
        'tar',
        '--create',
        '--zstd',
        '--numeric-owner',
        '--acls',
        '--xattrs',
        '--xattrs-include=*',
        '--sparse',
        '--file',
        str(partial),
        '--directory',
        '/',
        '--anchored',
        '--no-wildcards',
    ]
    for source in sources:
        for value in lines(item['excludes']):
            args.append('--exclude=' + str(Path(source) / value).lstrip('/'))
    # NUL-delimited names cannot be interpreted as tar options, even for odd names.
    with tempfile.TemporaryFile() as names:
        names.write(b''.join(p.lstrip('/').encode() + b'\0' for p in sources))
        names.seek(0)
        args += ['--null', '--verbatim-files-from', '--files-from', '-']
        proc = subprocess.Popen(args, stdin=names, start_new_session=True)
        try:
            while proc.poll() is None:
                check_space(partial.parent, 0, reserve)
                time.sleep(0.2)
            if proc.returncode:
                raise BackupError(f'Archive failed for {item["name"]} (tar exit {proc.returncode})')
        except BaseException:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            partial.unlink(missing_ok=True)
            raise
    with partial.open('rb') as stream:
        os.fsync(stream.fileno())


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish(item: BackupSet, partial: Path, instance: str, timestamp: str, gid: int) -> Path:
    subprocess.run(['zstd', '--test', '--quiet', str(partial)], check=True)
    final = partial.with_suffix('')
    metadata: Manifest = {
        'format': 1,
        'instanceuuid': instance,
        'setuuid': item['uuid'],
        'timestamp': timestamp,
        'archive': final.name,
        'size': partial.stat().st_size,
        'sha256': digest(partial),
    }
    os.chown(partial, 0, gid)
    os.chmod(partial, 0o640)
    # The run lock plus preflight collision checks protect this publication.
    if final.exists():
        raise BackupError('Archive name already exists')
    os.rename(partial, final)
    sync_directory(final.parent)
    manifest = final.with_name(final.name + '.manifest.json')
    atomic_json(manifest, metadata, 0o640)
    os.chown(manifest, 0, gid)
    return final
