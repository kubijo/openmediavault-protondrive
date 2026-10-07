"""Root archive orchestration; every entry point is supervised by systemd."""

import grp
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from .archive import archive, check_space, estimate, publish
from .common import STATE, BackupError, atomic_json, locked, request
from .completion import record_completion
from .config import load, remote_folder
from .containers import selection
from .json_data import JSONValue, decode, object_value
from .models import BackupSet, Configuration, Manifest
from .recovery import Recovery


def status(phase: str, **values: JSONValue) -> None:
    path = STATE / 'status.json'
    previous = object_value(decode(path.read_text())) if path.exists() else {}
    previous.update(phase=phase, message='')
    previous.update(values)
    atomic_json(path, previous)
    print(phase + (': ' + str(values.get('message', '')) if values.get('message') else ''), flush=True)


def cancelled(signum: int, frame: FrameType | None) -> None:
    raise BackupError(f'Backup cancelled by signal {signum}')


def receipt(path: Path) -> Path:
    return path.with_name(path.name + '.uploaded.json')


def upload(config: Configuration, item: BackupSet, path: Path) -> Manifest:
    status('uploading', message=path.name)
    result = request('upload', setuuid=item['uuid'], name=path.name)
    atomic_json(receipt(path), {'folder': remote_folder(config, item), 'sha256': result['sha256']})
    return result


def pending(config: Configuration, item: BackupSet, directory: Path) -> None:
    for manifest in sorted(directory.glob('*.tar.zst.manifest.json')):
        path = manifest.with_name(manifest.name.removesuffix('.manifest.json'))
        marker = receipt(path)
        if marker.exists() and object_value(decode(marker.read_text())).get('folder') == remote_folder(config, item):
            continue
        upload(config, item, path)


def prune_local(config: Configuration, item: BackupSet, directory: Path) -> None:
    confirmed: list[Path] = []
    for marker in directory.glob('*.tar.zst.uploaded.json'):
        path = marker.with_name(marker.name.removesuffix('.uploaded.json'))
        manifest = path.with_name(path.name + '.manifest.json')
        if not path.is_file() or path.is_symlink() or not manifest.is_file():
            continue
        value = object_value(decode(marker.read_text()))
        data = object_value(decode(manifest.read_text()))
        if value.get('folder') == remote_folder(config, item) and value.get('sha256') == data.get('sha256'):
            confirmed.append(path)
    for path in sorted(confirmed)[: max(0, len(confirmed) - item['localkeep'])]:
        path.unlink()
        path.with_name(path.name + '.manifest.json').unlink()
        receipt(path).unlink()


def run() -> None:
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
            groups: dict[tuple[str, ...] | None, list[BackupSet]] = {}
            for item in items:
                groups.setdefault(selection(item), []).append(item)
            gid = grp.getgrnam('protondrive').gr_gid
            staging = Path(config['stagingpath'])
            staging.mkdir(parents=True, exist_ok=True)
            os.chown(staging, 0, gid)
            os.chmod(staging, 0o750)
            request('probe')
            timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%MZ')
            required = 0
            for item in items:
                directory = staging / item['uuid']
                directory.mkdir(exist_ok=True)
                if directory.is_symlink():
                    raise BackupError('Staging set directory must not be a symlink')
                os.chown(directory, 0, gid)
                os.chmod(directory, 0o750)
                listing = request('prepare', setuuid=item['uuid'])
                pending(config, item, directory)
                name = f'{item["name"]}-{timestamp}.tar.zst'
                if (directory / name).exists() or any(e['name'] in (name, name + '.manifest.json') for e in listing):
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
                final = publish(item, partial, config['instanceuuid'], timestamp, gid)
                upload(config, item, final)
                results[item['name']] = {'archive': final.name, 'uploaded': datetime.now(UTC).isoformat()}
                status('retention', sets=results)
                request('prune', setuuid=item['uuid'])
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
                request('cancel-transfer')
            except (BackupError, OSError, ValueError, TypeError) as cancel_error:
                print(f'Could not cancel the Proton transfer: {cancel_error}', flush=True)
            try:
                recovery.restore()
            except Exception as recovery_error:
                status('recovery-failed', error=str(recovery_error))
                raise
            for _, partial in partials:
                partial.unlink(missing_ok=True)
            status('failed', error=str(exc), finished=datetime.now(UTC).isoformat())
            raise
