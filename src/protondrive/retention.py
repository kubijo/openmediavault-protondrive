"""Only verified archive/manifest pairs participate in remote retention."""

import re
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .archive import check_space, digest
from .common import BackupError
from .json_data import JSONValue, decode, is_object, object_value
from .models import BackupSet, Configuration, Manifest, RemoteEntry


class RemoteFiles(Protocol):
    def list(self, path: str) -> list[RemoteEntry]: ...
    def upload(self, path: str | Path, folder: str) -> None: ...
    def download(self, remote: str, directory: str | Path) -> None: ...
    def trash(self, folder: str, entry: RemoteEntry) -> None: ...


def metadata(value: object, config: Configuration, item: BackupSet, name: str) -> Manifest:
    if not is_object(value):
        raise BackupError('Invalid backup manifest')
    value = object_value(value)
    if (
        value.get('format') != 1
        or value.get('instanceuuid') != config['instanceuuid']
        or value.get('setuuid') != item['uuid']
        or value.get('archive') != name
    ):
        raise BackupError('Backup manifest identity mismatch')
    match = re.fullmatch(re.escape(item['name']) + r'-(\d{8}T\d{4}Z)\.tar\.zst', name)
    if not match or value.get('timestamp') != match[1]:
        raise BackupError('Invalid backup timestamp')
    datetime.strptime(match[1], '%Y%m%dT%H%M%z')
    size, sha256 = value.get('size'), value.get('sha256')
    if type(size) is not int or size <= 0 or not isinstance(sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', sha256):
        raise BackupError('Invalid backup size or digest')
    return {
        'format': 1,
        'instanceuuid': config['instanceuuid'],
        'setuuid': item['uuid'],
        'archive': name,
        'timestamp': match[1],
        'size': size,
        'sha256': sha256,
    }


def unique(listing: Sequence[RemoteEntry], name: str, kind: str = 'file') -> RemoteEntry:
    matches = [entry for entry in listing if entry['name'] == name]
    if len(matches) != 1 or matches[0]['type'] != kind:
        raise BackupError(f'Remote name is missing or ambiguous: {name}')
    return matches[0]


def read_remote_manifest(cli: RemoteFiles, folder: str, name: str) -> JSONValue:
    with tempfile.TemporaryDirectory(prefix='omv-proton-manifest-') as temporary:
        cli.download(folder + '/' + name, temporary)
        path = Path(temporary) / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise BackupError('Invalid downloaded manifest')
        return decode(path.read_text())


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
