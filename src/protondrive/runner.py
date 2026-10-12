"""Root archive orchestration; every entry point is supervised by systemd."""

import grp
import logging
import os
import shutil
import signal
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from .archive import archive, check_space, estimate, publish
from .common import STATE, BackupError, atomic_json, locked, request
from .completion import record_completion
from .compose_source import capture as capture_compose
from .compose_source import inventory as compose_inventory
from .compose_source import seal as seal_compose
from .compose_source import validate_source
from .config import lines, load, remote_folder
from .json_data import JSONValue, decode, object_value, validate
from .models import BackupSet, Configuration, Destination, Manifest
from .operation import Cancelled
from .recovery import Recovery

logger = logging.getLogger(__name__)
active_destination_id: str | None = None


def status(phase: str, **values: JSONValue) -> None:
    path = STATE / 'status.json'
    previous = object_value(decode(path.read_text())) if path.exists() else {}
    previous.update(phase=phase, message='', error='', errorcode='')
    previous.update(values)
    atomic_json(path, previous)
    print(phase + (': ' + str(values.get('message', '')) if values.get('message') else ''), flush=True)


def cancelled(signum: int, frame: FrameType | None) -> None:
    raise Cancelled(f'Backup cancelled by signal {signum}')


def receipt(path: Path, destination: Destination | None = None) -> Path:
    destination_id = 'protondrive' if destination is None else destination['id']
    suffix = '.uploaded.json' if destination_id == 'protondrive' else f'.uploaded.{destination_id}.json'
    return path.with_name(path.name + suffix)


def enabled_destinations(config: Configuration) -> list[Destination]:
    return [destination for destination in config['destinations'] if destination['enable']]


def confirmed(config: Configuration, item: BackupSet, path: Path, destination: Destination) -> bool:
    marker = receipt(path, destination)
    manifest = path.with_name(path.name + '.manifest.json')
    if not marker.is_file() or not path.is_file() or path.is_symlink() or not manifest.is_file():
        return False
    value = object_value(decode(marker.read_text()))
    data = object_value(decode(manifest.read_text()))
    return value.get('folder') == remote_folder(config, item, destination) and value.get('sha256') == data.get('sha256')


def upload(config: Configuration, item: BackupSet, path: Path, destination: Destination) -> Manifest:
    global active_destination_id
    status('uploading', message=f'{path.name} → {destination["name"]}')
    active_destination_id = destination['id']
    result = request('upload', destinationid=destination['id'], setuuid=item['uuid'], name=path.name)
    atomic_json(
        receipt(path, destination),
        {'folder': remote_folder(config, item, destination), 'sha256': result['sha256']},
    )
    active_destination_id = None
    return result


def upload_all(config: Configuration, item: BackupSet, path: Path) -> None:
    failures: list[BackupError] = []
    for destination in enabled_destinations(config):
        if confirmed(config, item, path, destination):
            continue
        try:
            upload(config, item, path, destination)
        except Cancelled:
            raise
        except BackupError as exc:
            failures.append(exc)
    if failures:
        raise failures[0]


def pending(config: Configuration, item: BackupSet, directory: Path) -> None:
    failures: list[BackupError] = []
    for manifest in sorted(directory.glob('*.tar.zst.manifest.json')):
        path = manifest.with_name(manifest.name.removesuffix('.manifest.json'))
        try:
            upload_all(config, item, path)
        except Cancelled:
            raise
        except BackupError as exc:
            failures.append(exc)
    if failures:
        raise failures[0]


def prune_local(config: Configuration, item: BackupSet, directory: Path) -> None:
    complete: list[Path] = []
    for path in directory.glob('*.tar.zst'):
        if all(confirmed(config, item, path, destination) for destination in enabled_destinations(config)):
            complete.append(path)
    for path in sorted(complete)[: max(0, len(complete) - item['localkeep'])]:
        path.unlink()
        path.with_name(path.name + '.manifest.json').unlink()
        for marker in directory.glob(path.name + '.uploaded*.json'):
            marker.unlink()


def run() -> None:
    global active_destination_id
    active_destination_id = None
    os.umask(0o077)
    with locked(STATE / 'run.lock'):
        signal.signal(signal.SIGTERM, cancelled)
        signal.signal(signal.SIGINT, cancelled)
        config = load()
        recovery = Recovery()
        partials: list[tuple[BackupSet, Path]] = []
        sealed_workspace: tempfile.TemporaryDirectory[str] | None = None
        paused = [item['name'] for item in config['sets'] if item['enable'] and item['migrationrequired']]
        try:
            status(
                'preflight',
                error='',
                started=datetime.now(UTC).isoformat(),
                capture='',
                paused=validate(paused),
                sources=[],
            )
            recovery.restore()
            items = [item for item in config['sets'] if item['enable'] and not item['migrationrequired']]
            if not items:
                raise BackupError('No runnable backup sets; review paused container-scoped sets')
            for destination in enabled_destinations(config):
                request('probe', destinationid=destination['id'])
            sources_by_job: dict[str, set[str]] = {}
            stack_owners: dict[str, str] = {}
            for item in items:
                for source in item['composesources']:
                    sources_by_job.setdefault(source['job_id'], set()).update(source['stacks'])
                    for stack in source['stacks']:
                        owner = stack_owners.setdefault(stack, source['job_id'])
                        if owner != source['job_id']:
                            raise BackupError(f'Compose stack {stack} is selected by different jobs')
            staging = Path(config['stagingpath'])
            compose = compose_inventory() if sources_by_job else None
            if sources_by_job and compose is None:
                raise BackupError('OMV Compose backup is not installed')
            if compose is not None:
                backup_folder = compose.folder.resolve()
                if backup_folder == staging or backup_folder in staging.parents or staging in backup_folder.parents:
                    raise BackupError('OMV Compose backup folder and staging directory must not overlap')
                for item in items:
                    for source_path in lines(item['paths']):
                        path = Path(source_path).resolve()
                        if path == backup_folder or path in backup_folder.parents or backup_folder in path.parents:
                            raise BackupError('OMV Compose backup folder overlaps a selected file source')
                for job_id, stacks in sources_by_job.items():
                    validate_source({'job_id': job_id, 'stacks': sorted(stacks)}, compose)
            gid = grp.getgrnam('protondrive').gr_gid
            staging.mkdir(parents=True, exist_ok=True)
            os.chown(staging, 0, gid)
            os.chmod(staging, 0o750)
            for stale in staging.glob('.compose-capture-*'):
                if stale.is_symlink() or not stale.is_dir():
                    raise BackupError('Invalid leftover Compose capture workspace')
                shutil.rmtree(stale)
            timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%MZ')
            for item in items:
                directory = staging / item['uuid']
                directory.mkdir(exist_ok=True)
                if directory.is_symlink():
                    raise BackupError('Staging set directory must not be a symlink')
                os.chown(directory, 0, gid)
                os.chmod(directory, 0o750)
                listings = [
                    request('prepare', destinationid=destination['id'], setuuid=item['uuid'])
                    for destination in enabled_destinations(config)
                ]
                pending(config, item, directory)
                name = f'{item["name"]}-{timestamp}.tar.zst'
                if (directory / name).exists() or any(
                    e['name'] in (name, name + '.manifest.json') for listing in listings for e in listing
                ):
                    raise BackupError('A backup already exists for this minute; try again next minute')
                for stale in directory.glob('*.tar.zst.partial'):
                    stale.unlink()
            sealed_by_job: dict[str, tuple[Path, ...]] = {}
            if compose is not None:
                sealed_workspace = tempfile.TemporaryDirectory(prefix='.compose-capture-', dir=staging)
                for job_id, stacks in sources_by_job.items():
                    status('capturing-compose', message=', '.join(sorted(stacks)), capture='unverified')
                    captured = capture_compose(
                        {'job_id': job_id, 'stacks': sorted(stacks)},
                        compose,
                        staging / f'compose-{job_id}.log',
                    )
                    status('sealing-compose', message=', '.join(sorted(stacks)))
                    include_global_env = (
                        compose.source_folder is not None
                        and (compose.source_folder / 'global.env').is_file()
                        and not (compose.source_folder / 'global.env').is_symlink()
                    )
                    sealed_by_job[job_id] = seal_compose(
                        captured,
                        Path(sealed_workspace.name) / job_id,
                        include_global_env=include_global_env,
                    )
            extra_by_set: dict[str, tuple[Path, ...]] = {}
            records_by_set: dict[str, list[dict[str, JSONValue]]] = {}
            required = 0
            for item in items:
                extras = tuple(
                    path
                    for source in item['composesources']
                    for path in sealed_by_job[source['job_id']]
                    if path.name in source['stacks'] or path.name == 'global.env'
                )
                extra_by_set[item['uuid']] = extras
                records_by_set[item['uuid']] = (
                    [object_value({'kind': 'files', 'paths': item['paths'].splitlines(), 'capture': 'live'})]
                    if item['paths'].strip()
                    else []
                )
                records_by_set[item['uuid']].extend(
                    object_value(
                        {
                            'kind': 'omv-compose',
                            'job_id': source['job_id'],
                            'stacks': source['stacks'],
                            'capture': 'unverified',
                        }
                    )
                    for source in item['composesources']
                )
                required += estimate(item, extras)
            check_space(staging, required, config['minimumfreebytes'])
            for item in items:
                partial = staging / item['uuid'] / f'{item["name"]}-{timestamp}.tar.zst.partial'
                status('archiving', message=item['name'], sources=validate(records_by_set[item['uuid']]))
                partials.append((item, partial))
                archive(item, partial, config['minimumfreebytes'], extra_by_set[item['uuid']])
            results: dict[str, JSONValue] = {}
            for item, partial in partials:
                status('verifying', message=item['name'])
                final = publish(
                    item,
                    partial,
                    config['instanceuuid'],
                    timestamp,
                    gid,
                    source_records=records_by_set[item['uuid']],
                )
                upload_all(config, item, final)
                results[item['name']] = {
                    'archive': final.name,
                    'uploaded': datetime.now(UTC).isoformat(),
                    'capture': 'unverified' if item['composesources'] else 'live',
                }
                status('retention', sets=results)
                for destination in enabled_destinations(config):
                    request('prune', destinationid=destination['id'], setuuid=item['uuid'])
                prune_local(config, item, final.parent)
            completed = datetime.now(UTC).isoformat()
            record_completion(STATE, completed)
            status(
                'completed',
                sets=results,
                paused=validate(paused),
                lastsuccess=completed,
                finished=datetime.now(UTC).isoformat(),
            )
        except BaseException as exc:
            # Do not let repeated termination interrupt container recovery.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                if active_destination_id is not None:
                    request('cancel-transfer', destinationid=active_destination_id)
            except (BackupError, OSError, ValueError, TypeError) as cancel_error:
                print(f'Could not cancel the storage transfer: {cancel_error}', flush=True)
            try:
                recovery.restore()
            except Exception:
                reference = uuid.uuid4().hex[:12]
                logger.exception('Container recovery failed; reference %s', reference)
                status(
                    'recovery-failed',
                    error=f'Container recovery failed; reference {reference}',
                    errorcode='internal',
                )
                raise
            for _, partial in partials:
                partial.unlink(missing_ok=True)
            if isinstance(exc, BackupError):
                message = str(exc)
                code = exc.code
            else:
                reference = uuid.uuid4().hex[:12]
                logger.exception('Backup failed unexpectedly; reference %s', reference)
                message = f'Backup failed unexpectedly; reference {reference}'
                code = 'internal'
            status('failed', error=message, errorcode=code, finished=datetime.now(UTC).isoformat())
            raise
        finally:
            if sealed_workspace is not None:
                sealed_workspace.cleanup()
