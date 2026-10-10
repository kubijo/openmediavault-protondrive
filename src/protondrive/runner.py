"""Root archive orchestration; every entry point is supervised by systemd."""

import grp
import json
import logging
import os
import signal
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from .archive import archive, check_space, estimate, publish
from .common import STATE, BackupError, atomic_json, locked, request
from .completion import record_completion
from .compose import ProjectCapture, capture, pull
from .compose import manifest as compose_manifest
from .config import load, remote_folder
from .containers import selection
from .json_data import JSONValue, decode, object_value
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
        try:
            status('preflight', error='', started=datetime.now(UTC).isoformat())
            recovery.restore()
            items = [item for item in config['sets'] if item['enable']]
            if not items:
                raise BackupError('No backup sets are enabled')
            for destination in enabled_destinations(config):
                request('probe', destinationid=destination['id'])
            captures: dict[str, tuple[ProjectCapture, ...]] = {}
            for item in items:
                if item.get('composeapps'):
                    status('capturing-compose', message=item['name'])
                    captures[item['uuid']] = capture(item)
                    if len(json.dumps(compose_manifest(captures[item['uuid']])).encode()) > 1047552:
                        raise BackupError('Compose application metadata exceeds the manifest size limit')
                    status('pulling-compose-images', message=item['name'])
                    pull(captures[item['uuid']])
            groups: dict[tuple[str, ...] | None, list[BackupSet]] = {}
            for item in items:
                groups.setdefault(selection(item), []).append(item)
            gid = grp.getgrnam('protondrive').gr_gid
            staging = Path(config['stagingpath'])
            staging.mkdir(parents=True, exist_ok=True)
            os.chown(staging, 0, gid)
            os.chmod(staging, 0o750)
            timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%MZ')
            required = 0
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
                required += estimate(item)
            check_space(staging, required, config['minimumfreebytes'])
            for selected, group in groups.items():
                sensitive = selected is None or bool(selected)
                try:
                    if sensitive:
                        status('stopping-containers')
                        if selected is None:
                            recovery.stop(config['containerstoptimeout'])
                        else:
                            recovery.stop(config['containerstoptimeout'], selected)
                    for item in group:
                        if selected is not None and selection(item) != selected:
                            raise BackupError(
                                'Application containers changed during backup preflight; retry after deployment finishes'
                            )
                        partial = staging / item['uuid'] / f'{item["name"]}-{timestamp}.tar.zst.partial'
                        status('archiving', message=item['name'])
                        partials.append((item, partial))
                        archive(item, partial, config['minimumfreebytes'])
                finally:
                    if sensitive:
                        status('recovering-containers')
                        recovery.restore()
            results: dict[str, JSONValue] = {}
            for item, partial in partials:
                status('verifying', message=item['name'])
                final = publish(item, partial, config['instanceuuid'], timestamp, gid, captures.get(item['uuid'], ()))
                upload_all(config, item, final)
                results[item['name']] = {'archive': final.name, 'uploaded': datetime.now(UTC).isoformat()}
                status('retention', sets=results)
                for destination in enabled_destinations(config):
                    request('prune', destinationid=destination['id'], setuuid=item['uuid'])
                prune_local(config, item, final.parent)
            completed = datetime.now(UTC).isoformat()
            record_completion(STATE, completed)
            status(
                'completed',
                sets=results,
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
