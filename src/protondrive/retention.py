"""Only verified archive/manifest pairs participate in remote retention."""

import re
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .archive import check_space, digest
from .backend import RemoteFiles
from .common import BackupError
from .compose import manifest_projects
from .json_data import JSONValue, decode, is_object, object_value
from .models import BackupSet, Configuration, Manifest, RemoteEntry


def metadata(value: object, config: Configuration, item: BackupSet, name: str) -> Manifest:
    return archive_metadata(value, config['instanceuuid'], item['uuid'], name, item['name'])


def archive_metadata(value: object, instance: str, set_id: str, name: str, set_name: str | None = None) -> Manifest:
    if not is_object(value):
        raise BackupError('Invalid backup manifest', code='invalid_archive')
    value = object_value(value)
    if (
        type(value.get('format')) is not int
        or value.get('format') not in (1, 2)
        or value.get('instanceuuid') != instance
        or value.get('setuuid') != set_id
        or value.get('archive') != name
    ):
        raise BackupError('Backup manifest identity mismatch', code='invalid_archive')
    prefix = re.escape(set_name) if set_name is not None else r'[A-Za-z][A-Za-z0-9_-]{0,63}'
    match = re.fullmatch(prefix + r'-(\d{8}T\d{4}Z)\.tar\.zst', name)
    if not match or value.get('timestamp') != match[1]:
        raise BackupError('Invalid backup timestamp', code='invalid_archive')
    try:
        datetime.strptime(match[1], '%Y%m%dT%H%M%z')
    except ValueError as exc:
        raise BackupError('Invalid backup timestamp', code='invalid_archive') from exc
    size, sha256 = value.get('size'), value.get('sha256')
    if type(size) is not int or size <= 0 or not isinstance(sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', sha256):
        raise BackupError('Invalid backup size or digest', code='invalid_archive')
    result = Manifest(
        format=2 if value['format'] == 2 else 1,
        instanceuuid=instance,
        setuuid=set_id,
        archive=name,
        timestamp=match[1],
        size=size,
        sha256=sha256,
    )
    if result['format'] == 2:
        try:
            result['compose'] = manifest_projects(value['compose'])
        except (KeyError, TypeError, ValueError, BackupError) as exc:
            raise BackupError('Invalid Compose manifest', code='invalid_archive') from exc
    elif 'compose' in value:
        raise BackupError('Unexpected Compose metadata in legacy manifest', code='invalid_archive')
    return result


def unique(listing: Sequence[RemoteEntry], name: str, kind: str = 'file') -> RemoteEntry:
    matches = [entry for entry in listing if entry['name'] == name]
    if len(matches) != 1 or matches[0]['type'] != kind:
        raise BackupError(f'Remote name is missing or ambiguous: {name}')
    return matches[0]


def read_remote_manifest(cli: RemoteFiles, folder: str, name: str) -> JSONValue:
    with tempfile.TemporaryDirectory(prefix='omv-proton-manifest-') as temporary:
        cli.download(folder + '/' + name, temporary)
        path = Path(temporary) / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
            raise BackupError('Invalid downloaded manifest', code='invalid_archive')
        try:
            return decode(path.read_text())
        except (UnicodeError, ValueError) as exc:
            raise BackupError('Invalid downloaded manifest', code='invalid_archive') from exc


def verify_remote_archive(cli: RemoteFiles, config: Configuration, folder: str, value: Manifest) -> None:
    name = value['archive']
    with tempfile.TemporaryDirectory(prefix='omv-proton-verify-') as temporary:
        check_space(temporary, value['size'], config['minimumfreebytes'])
        cli.download(folder + '/' + name, temporary)
        downloaded = Path(temporary) / name
        if downloaded.is_symlink() or not downloaded.is_file():
            raise BackupError('Remote archive download is not a regular file')
        if downloaded.stat().st_size != value['size'] or digest(downloaded) != value['sha256']:
            raise BackupError('Remote archive failed checksum verification')


def upload_pair(cli: RemoteFiles, config: Configuration, item: BackupSet, archive: Path, folder: str) -> Manifest:
    manifest = archive.with_name(archive.name + '.manifest.json')
    value = metadata(decode(manifest.read_text()), config, item, archive.name)
    if archive.stat().st_size != value['size'] or digest(archive) != value['sha256']:
        raise BackupError('Local archive failed checksum verification')
    listing = cli.list(folder)
    existing = [e for e in listing if e['name'] == archive.name]
    if existing:
        entry = unique(listing, archive.name)
        if entry['size'] != value['size']:
            raise BackupError('Existing remote archive has a different size')
        if any(e['name'] == manifest.name for e in listing):
            unique(listing, manifest.name)
            if read_remote_manifest(cli, folder, manifest.name) != value:
                raise BackupError('Existing remote manifest does not match')
            verify_remote_archive(cli, config, folder, value)
            return value
    else:
        cli.upload(archive, folder)
    entry = unique(cli.list(folder), archive.name)
    if entry['size'] != value['size']:
        raise BackupError('Remote upload size could not be verified')
    verify_remote_archive(cli, config, folder, value)
    cli.upload(manifest, folder)
    unique(cli.list(folder), manifest.name)
    if read_remote_manifest(cli, folder, manifest.name) != value:
        raise BackupError('Remote completion manifest could not be verified')
    return value


def prune_remote(cli: RemoteFiles, config: Configuration, item: BackupSet, folder: str) -> int:
    listing = cli.list(folder)
    backups: list[tuple[RemoteEntry, RemoteEntry]] = []
    pattern = re.compile(re.escape(item['name']) + r'-\d{8}T\d{4}Z\.tar\.zst')
    for entry in listing:
        if not pattern.fullmatch(entry['name']):
            continue
        archive = unique(listing, entry['name'])
        manifest_name = entry['name'] + '.manifest.json'
        if not any(e['name'] == manifest_name for e in listing):
            continue
        manifest = unique(listing, manifest_name)
        value = metadata(read_remote_manifest(cli, folder, manifest_name), config, item, archive['name'])
        if value['size'] != archive['size']:
            raise BackupError('Remote backup size disagrees with its manifest')
        backups.append((archive, manifest))
    backups.sort(key=lambda pair: pair[0]['name'])
    for archive, manifest in backups[: max(0, len(backups) - item['remotekeep'])]:
        # Both objects remain identifiable; partial cleanup is reported, not hidden.
        cli.trash(folder, archive)
        cli.trash(folder, manifest)
    return len(backups)
