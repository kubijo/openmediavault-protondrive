"""Read-only remote discovery and private, verified archive inspection."""

import logging
import os
import selectors
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from protondrive.archive import check_space, digest
from protondrive.common import STATE, BackupError, locked, request, sync_directory
from protondrive.config import identifier, load
from protondrive.json_data import JSONValue
from protondrive.operation import OperationControl
from protondrive.restore import extract, inspect, protected_directory, select
from protondrive.restore_download import CACHE as DOWNLOAD_CACHE
from protondrive.restore_journal import reconcile
from protondrive.retention import archive_metadata

from .store import Store
from .v1 import control_pb2 as wire

CACHE = STATE / 'restore-cache'
logger = logging.getLogger(__name__)


def copy_download(source: Path, destination: Path, size: int, control: OperationControl | None = None) -> None:
    """Pin every directory and file without following unprivileged symlinks."""
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in source.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                raise BackupError(
                    'Downloaded archive is not a regular file with the declared size', code='invalid_archive'
                )
            remaining = size
            with destination.open('xb') as target:
                os.fchmod(target.fileno(), 0o600)
                while remaining:
                    if control is not None:
                        control.check()
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise BackupError('Downloaded archive was truncated', code='invalid_archive')
                    target.write(chunk)
                    remaining -= len(chunk)
                if stream.read(1):
                    raise BackupError('Downloaded archive exceeds its declared size', code='invalid_archive')
                target.flush()
                os.fsync(target.fileno())
    finally:
        os.close(parent)


def decompress(
    source: Path,
    destination: Path,
    reserve: int,
    timeout: int,
    control: OperationControl | None = None,
) -> None:
    """Bound time, memory and output space without blocking on a pipe read."""
    deadline = time.monotonic() + timeout
    with destination.open('xb') as target, tempfile.TemporaryFile(dir=destination.parent) as errors:
        proc = subprocess.Popen(
            ['zstd', '--decompress', '--stdout', '--memory=256MB', '--', str(source)],
            stdout=subprocess.PIPE,
            stderr=errors,
            start_new_session=True,
        )
        try:
            if proc.stdout is None:
                raise BackupError('Decompressor did not open its output pipe')
            with proc.stdout, selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    if control is not None:
                        control.check()
                    if time.monotonic() > deadline:
                        raise BackupError('Archive decompression timed out')
                    if not selector.select(timeout=0.2):
                        continue
                    chunk = os.read(proc.stdout.fileno(), 1024 * 1024)
                    if not chunk:
                        break
                    check_space(destination.parent, len(chunk), reserve)
                    target.write(chunk)
                if proc.wait(timeout=max(0.1, deadline - time.monotonic())):
                    raise BackupError('Archive decompression failed', code='invalid_archive')
            target.flush()
            os.fsync(target.fileno())
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


class Archives:
    def __init__(self, store: Store, cache: Path = CACHE) -> None:
        self.store = store
        self.cache = cache

    def directory(self, job: str) -> Path:
        return self.cache / identifier(job)

    def inspect(
        self,
        job: str,
        value: wire.ArchiveReference,
        progress: Callable[[str], None],
        control: OperationControl,
    ) -> None:
        config = load()
        self.cache.mkdir(mode=0o700, exist_ok=True)
        if sum(1 for _ in self.cache.iterdir()) >= 8:
            raise BackupError('Eight inspected archives are cached; release an archive before downloading another')
        directory = self.directory(job)
        with locked(STATE / 'run.lock'):
            directory.mkdir(mode=0o700)
            try:
                control.check()
                progress('Downloading archive and completion manifest')
                manifest = request(
                    'download-archive',
                    timeout=2 * config['transfertimeout'] + 60,
                    **({'destinationid': value.destination_id} if value.destination_id else {}),
                    instanceuuid=value.instance_id,
                    setuuid=value.set_id,
                    jobuuid=job,
                    name=value.name,
                )
                manifest = archive_metadata(manifest, value.instance_id, value.set_id, value.name)
                control.check()
                compressed = directory / 'archive.tar.zst'
                progress('Verifying downloaded checksum')
                check_space(directory, manifest['size'], config['minimumfreebytes'])
                copy_download(DOWNLOAD_CACHE / job / value.name, compressed, manifest['size'], control)
                if digest(compressed, control.check) != manifest['sha256']:
                    raise BackupError('Downloaded archive failed checksum verification', code='invalid_archive')
                progress('Decompressing and inspecting archive paths')
                tar = directory / 'archive.tar'
                decompress(compressed, tar, config['minimumfreebytes'], config['transfertimeout'], control)
                try:
                    members = inspect(tar, control)
                except BackupError as exc:
                    raise BackupError(str(exc), code='invalid_archive') from exc
                except tarfile.TarError as exc:
                    raise BackupError('Archive contains invalid tar data', code='invalid_archive') from exc
                entries = {entry.name: entry for entry in members}
                for project in manifest.get('compose', []):
                    for path in project['definitions'] + project['envfiles'] + project['secretfiles']:
                        entry = entries.get(path.lstrip('/'))
                        if entry is None or entry.kind != 'file' or entry.issue:
                            raise BackupError(
                                'Compose manifest refers to a missing or invalid file', code='invalid_archive'
                            )
                    for service in project['services']:
                        for bind in service['binds']:
                            entry = entries.get(bind['source'].lstrip('/'))
                            if entry is None or entry.kind not in ('file', 'directory') or entry.issue:
                                raise BackupError(
                                    'Compose manifest refers to a missing bind source', code='invalid_archive'
                                )
                result = wire.GetArchiveResponse(
                    members=[
                        wire.ArchiveMember(
                            path=entry.name,
                            kind=entry.kind,
                            size=entry.size,
                            link_target=entry.target,
                            issue=entry.issue,
                        )
                        for entry in members
                    ],
                    total=len(members),
                    compose=[
                        wire.ComposeProjectSnapshot(
                            project=project['project'],
                            definitions=project['definitions'],
                            env_files=project['envfiles'],
                            secret_files=project['secretfiles'],
                            services=[
                                wire.ComposeServiceSnapshot(
                                    service=service['service'],
                                    image=service['image'],
                                    pinned=service['pinned'],
                                    replicas=service['replicas'],
                                    binds=[
                                        wire.ComposeBindSnapshot(
                                            source=bind['source'],
                                            target=bind['target'],
                                            read_only=bind['read_only'],
                                        )
                                        for bind in service['binds']
                                    ],
                                )
                                for service in project['services']
                            ],
                        )
                        for project in manifest.get('compose', [])
                    ],
                )
                with (directory / 'index.pb').open('xb') as output:
                    output.write(result.SerializeToString())
                    output.flush()
                    os.fsync(output.fileno())
                compressed.unlink()
                sync_directory(directory)
                control.commit(lambda: None)
            except BaseException:
                shutil.rmtree(directory)
                raise
            finally:
                request('discard-download', timeout=30, jobuuid=job)

    def ready(self, job: str) -> Path:
        record = self.store.get(job)
        if record.operation != wire.OPERATION_INSPECT_ARCHIVE or record.state != wire.JOB_STATE_SUCCEEDED:
            raise ValueError('Archive inspection has not completed successfully')
        directory = self.directory(job)
        if not directory.is_dir():
            raise ValueError('This inspected archive has been released; download it again')
        return directory

    def get(self, value: wire.GetArchiveRequest) -> wire.GetArchiveResponse:
        directory = self.ready(value.inspection_id)
        result = wire.GetArchiveResponse.FromString((directory / 'index.pb').read_bytes())
        end = min(value.offset + 100, result.total)
        if value.offset > result.total:
            raise ValueError('Archive offset is outside the inspected index')
        return wire.GetArchiveResponse(
            members=result.members[value.offset : end],
            total=result.total,
            next_offset=end if end < result.total else 0,
            compose=result.compose,
        )

    def preview(self, value: wire.FileExtraction) -> wire.PreviewExtractionResponse:
        directory = self.ready(value.inspection_id)
        entries = select(inspect(directory / 'archive.tar'), value.paths)
        destination = Path(value.destination)
        with protected_directory(destination.parent) as parent:
            stable = Path(f'/proc/self/fd/{parent}')
            if os.path.lexists(stable / destination.name):
                raise ValueError('Extraction destination already exists')
            required = sum(entry.size + 4096 for entry in entries)
            check_space(stable, required, load()['minimumfreebytes'])
        selected = set(value.paths)
        # Bound the wire response: directory contents are summarized by count;
        # only automatically included parents and hard-link targets are listed.
        prefixes = [entry.name + '/' for entry in entries if entry.name in selected and entry.kind == 'directory']
        dependencies = [
            entry.name
            for entry in entries
            if entry.name not in selected and not any(entry.name.startswith(prefix) for prefix in prefixes)
        ]
        if len(dependencies) > 1000 or sum(len(name.encode()) for name in dependencies) > 1024 * 1024:
            raise ValueError('Selection requires too many implicit dependencies; select their parent directory')
        return wire.PreviewExtractionResponse(
            entries=len(entries),
            required_bytes=required,
            included_dependencies=dependencies,
            destination=str(destination),
        )

    def release(self, job: str) -> None:
        if self.store.active():
            raise ValueError('Wait for the current operation before releasing an inspected archive')
        self.purge(job)

    def list(self) -> wire.ListArchivesResponse:
        result = wire.ListArchivesResponse()
        if self.cache.exists():
            for directory in sorted(self.cache.iterdir()):
                job = self.store.get(directory.name)
                parameters = self.store.parameters(job.id)
                result.archives.append(
                    wire.CachedArchive(
                        inspection_id=job.id,
                        source=parameters.inspect_archive,
                        ready=job.state == wire.JOB_STATE_SUCCEEDED,
                    )
                )
        return result

    def purge(self, job: str) -> None:
        directory = self.directory(job)
        if directory.is_symlink():
            raise BackupError('Archive cache is not a private directory')
        if directory.exists():
            info = directory.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise BackupError('Archive cache ownership or permissions changed')
            shutil.rmtree(directory)
            sync_directory(self.cache)

    @staticmethod
    def discard_download(job: str) -> None:
        deadline = time.monotonic() + 5
        while True:
            try:
                request('discard-download', timeout=5, jobuuid=job)
                return
            except BackupError as exc:
                if exc.code != 'busy' or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

    def resolve_extraction(self, job: str, state: wire.JobState.ValueType, detail: str, failure_code: str = '') -> None:
        """Resolve filesystem evidence before retiring its recovery journal."""
        try:
            with locked(STATE / 'run.lock'):
                publication = self.store.publication(job)
                completed = publication is not None and reconcile(publication)
                self.store.update(
                    job,
                    wire.JOB_STATE_SUCCEEDED if completed else state,
                    'Files were published; recovery verified the destination' if completed else detail,
                    failure_code='' if completed else failure_code,
                    clear_publication=True,
                )
        except (OSError, ValueError, BackupError) as exc:
            # Keep the journal for another recovery attempt, including when the
            # job is already interrupted. Never hide a failed cleanup as cancel.
            logger.exception('Extraction recovery failed for operation %s', job)
            message = (
                f'Recovery requires attention: {exc}; {detail}'
                if isinstance(exc, BackupError)
                else f'Recovery requires attention; reference {job}'
            )
            self.store.update(
                job,
                wire.JOB_STATE_INTERRUPTED,
                message,
                failure_code=exc.code if isinstance(exc, BackupError) else 'internal',
            )

    def recover(self) -> None:
        for job in self.store.recovery_jobs():
            try:
                if job.operation == wire.OPERATION_EXTRACT_FILES:
                    terminal = job.state in (wire.JOB_STATE_FAILED, wire.JOB_STATE_CANCELLED)
                    self.resolve_extraction(
                        job.id,
                        job.state if terminal else wire.JOB_STATE_INTERRUPTED,
                        job.message if terminal else 'Interrupted extraction cleaned up',
                        job.failure_code if terminal else '',
                    )
                elif job.operation == wire.OPERATION_INSPECT_ARCHIVE:
                    request('cancel-download', timeout=5, jobuuid=job.id)
                    self.discard_download(job.id)
                    self.purge(job.id)
                    self.store.update(
                        job.id, wire.JOB_STATE_INTERRUPTED, 'Inspection interrupted; download the archive again'
                    )
            except (OSError, ValueError, BackupError) as exc:
                logger.exception('Archive recovery failed for operation %s', job.id)
                message = (
                    f'Recovery requires attention: {exc}'
                    if isinstance(exc, BackupError)
                    else f'Recovery requires attention; reference {job.id}'
                )
                self.store.update(
                    job.id,
                    wire.JOB_STATE_INTERRUPTED,
                    message,
                    failure_code=exc.code if isinstance(exc, BackupError) else 'internal',
                )

    def extract(
        self,
        job: str,
        value: wire.FileExtraction,
        progress: Callable[[str], None],
        control: OperationControl,
    ) -> None:
        directory = self.ready(value.inspection_id)
        with locked(STATE / 'run.lock'):
            extract(
                directory / 'archive.tar',
                value.paths,
                Path(value.destination),
                load()['minimumfreebytes'],
                progress,
                control=control,
                checkpoint=lambda state: self.store.checkpoint(job, state),
            )


def browse(value: wire.BrowseBackupsRequest) -> wire.BrowseBackupsResponse:
    parameters: dict[str, JSONValue] = {}
    if value.destination_id:
        parameters['destinationid'] = value.destination_id
    if value.HasField('instance_id'):
        parameters['instanceuuid'] = value.instance_id
    if value.HasField('set_id'):
        parameters['setuuid'] = value.set_id
    entries = request('browse', timeout=25, **parameters)
    names = [entry['name'] for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError('Remote names are ambiguous; inspect the directory in Proton Drive')
    result: list[wire.BackupEntry] = []
    for entry in entries:
        name = entry['name']
        complete = (
            entry['type'] == 'file'
            and name.endswith('.tar.zst')
            and sum(item['name'] == name and item['type'] == 'file' for item in entries) == 1
            and sum(item['name'] == name + '.manifest.json' and item['type'] == 'file' for item in entries) == 1
        )
        item = wire.BackupEntry(name=name, directory=entry['type'] == 'folder', complete_pair=complete)
        if entry['size'] is not None:
            item.size = entry['size']
        result.append(item)
    return wire.BrowseBackupsResponse(
        entries=result, foreign_instance=(value.HasField('instance_id') and value.instance_id != load()['instanceuuid'])
    )
