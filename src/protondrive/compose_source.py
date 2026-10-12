"""Read OMV Compose configuration and consume its completed rsync copies."""

import json
import re
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from .common import BackupError
from .json_data import JSONValue, decode, integer, object_value, string
from .models import ComposeSource
from .operation import Cancelled

BACKUP_COMMAND = Path('/usr/sbin/omv-compose-backup-multi')
STACK_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')


@dataclass(frozen=True)
class ComposeJob:
    id: str
    name: str
    enabled: bool
    backup: bool
    update: bool
    prune: bool
    skip_start_stop: bool
    exclude_filter: str


@dataclass(frozen=True)
class ComposeInventory:
    folder: Path
    jobs: tuple[ComposeJob, ...]
    stacks: tuple[str, ...]
    max_volume_size: int
    source_folder: Path | None = None


def rpc(actor: str, service: str, method: str, params: dict[str, JSONValue]) -> JSONValue:
    result = subprocess.run(
        ['/usr/sbin/omv-rpc', '-u', actor, service, method, json.dumps(params)],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise BackupError(f'OMV {service}.{method} failed; inspect the service journal')
    return decode(result.stdout)


def rows(value: JSONValue) -> list[dict[str, JSONValue]]:
    data = object_value(value).get('data')
    if not isinstance(data, list):
        raise BackupError('OMV Compose returned an invalid list')
    return [object_value(item) for item in data]


def inventory(actor: str = 'admin') -> ComposeInventory | None:
    if not BACKUP_COMMAND.is_file():
        return None
    settings = object_value(rpc(actor, 'Compose', 'get', {}))
    if settings.get('backupbackend', 'rsync') != 'rsync':
        raise BackupError('Set OMV Compose backup backend to rsync for this integration')
    ref = settings.get('backupsharedfolderref')
    if not isinstance(ref, str) or not ref:
        raise BackupError('OMV Compose has no backup shared folder')
    folder_value = rpc(actor, 'ShareMgmt', 'getPath', {'uuid': ref})
    if not isinstance(folder_value, str) or not folder_value.startswith('/'):
        raise BackupError('OMV Compose returned an invalid backup folder')
    source_ref = settings.get('sharedfolderref')
    if not isinstance(source_ref, str) or not source_ref:
        raise BackupError('OMV Compose has no source shared folder')
    source_folder_value = rpc(actor, 'ShareMgmt', 'getPath', {'uuid': source_ref})
    if not isinstance(source_folder_value, str) or not source_folder_value.startswith('/'):
        raise BackupError('OMV Compose returned an invalid source folder')
    jobs = rows(
        rpc(actor, 'Compose', 'getJobList', {'start': 0, 'limit': 10000, 'sortfield': 'uuid', 'sortdir': 'ASC'})
    )
    try:
        names = rpc(actor, 'Compose', 'getFileListSuggest', {})
    except BackupError:
        files = rows(
            rpc(actor, 'Compose', 'getFileList', {'start': 0, 'limit': 10000, 'sortfield': 'name', 'sortdir': 'ASC'})
        )
        names = [file.get('name') for file in files]
    if not isinstance(names, list):
        raise BackupError('OMV Compose returned an invalid stack list')
    return ComposeInventory(
        folder=Path(folder_value),
        jobs=tuple(
            ComposeJob(
                id=str(uuid.UUID(string(job['uuid']))),
                name=string(job.get('comment') or job['uuid']),
                enabled=job.get('enable') is True,
                backup=job.get('backup') is True,
                update=job.get('update') is True,
                prune=job.get('prune') is True,
                skip_start_stop=job.get('skipstartstop') is True,
                exclude_filter=string(job.get('excludefilter', '')),
            )
            for job in jobs
        ),
        stacks=tuple(
            name for name in names if isinstance(name, str) and len(name) <= 128 and STACK_NAME.fullmatch(name)
        ),
        max_volume_size=integer(settings.get('backupmaxsize', 0)) * 1024**3,
        source_folder=Path(source_folder_value),
    )


def validate_source(source: ComposeSource, available: ComposeInventory) -> None:
    job = next((job for job in available.jobs if job.id == source['job_id']), None)
    if job is None or not job.backup or job.update or job.prune:
        raise BackupError('Select an OMV Compose backup-only job')
    if job.enabled:
        raise BackupError('Disable the OMV Compose job schedule before using it here')
    if job.skip_start_stop or job.exclude_filter:
        raise BackupError('OMV Compose job must stop containers and have no stack exclusion filter')
    missing = set(source['stacks']) - set(available.stacks)
    if missing:
        raise BackupError('OMV Compose stack is no longer configured: ' + ', '.join(sorted(missing)))
    if not available.folder.is_dir() or available.folder.is_symlink():
        raise BackupError('OMV Compose backup folder is unavailable')


def capture(source: ComposeSource, available: ComposeInventory, log: Path) -> tuple[Path, ...]:
    validate_source(source, available)
    cancelled = False
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    def defer_cancel(signum: int, frame: FrameType | None) -> None:
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, defer_cancel)
    signal.signal(signal.SIGINT, defer_cancel)
    try:
        with log.open('wb') as output:
            process = subprocess.Popen(
                [str(BACKUP_COMMAND), '-f', ','.join(source['stacks']), '-u', source['job_id']],
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            while process.poll() is None:
                time.sleep(0.2)
            if process.returncode:
                raise BackupError(f'OMV Compose backup failed (exit {process.returncode}); see the capture log')
        if cancelled:
            raise Cancelled('Backup cancelled after OMV Compose finished')
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
    paths = tuple(available.folder / name for name in source['stacks'])
    if any(not path.is_dir() or path.is_symlink() for path in paths):
        raise BackupError('OMV Compose did not produce every selected stack folder')
    return paths


def seal(paths: tuple[Path, ...], destination: Path, *, include_global_env: bool = False) -> tuple[Path, ...]:
    """Copy selected Compose output so later OMV jobs cannot replace it during archiving."""
    destination.mkdir(mode=0o700)
    sealed: list[Path] = []
    for path in paths:
        target = destination / path.name
        target.mkdir(mode=0o700)
        result = subprocess.run(
            ['rsync', '-aHAX', '--numeric-ids', '--', str(path) + '/', str(target) + '/'],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise BackupError(f'Could not seal OMV Compose output (rsync exit {result.returncode})')
        sealed.append(target)
    global_env = paths[0].parent / 'global.env' if paths else None
    if include_global_env and (global_env is None or not global_env.is_file() or global_env.is_symlink()):
        raise BackupError('OMV Compose did not produce its global environment file')
    if include_global_env and global_env is not None:
        result = subprocess.run(
            ['rsync', '-aAX', '--numeric-ids', '--', str(global_env), str(destination / 'global.env')],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise BackupError(f'Could not seal OMV Compose environment (rsync exit {result.returncode})')
        sealed.append(destination / 'global.env')
    return tuple(sealed)
