"""Only verified archive/manifest pairs participate in remote retention."""

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .archive import check_space, digest
from .common import BackupError


def metadata(value, config, item, name):
    if not isinstance(value, dict):
        raise BackupError('Invalid backup manifest')
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
    if (
        type(value.get('size')) is not int
        or value['size'] <= 0
        or not re.fullmatch(r'[a-f0-9]{64}', value.get('sha256', ''))
    ):
        raise BackupError('Invalid backup size or digest')
    return value


def unique(listing, name, kind='file'):
    matches = [entry for entry in listing if entry['name'] == name]
    if len(matches) != 1 or matches[0]['type'] != kind:
        raise BackupError(f'Remote name is missing or ambiguous: {name}')
    return matches[0]


def read_remote_manifest(cli, folder, name):
    with tempfile.TemporaryDirectory(prefix='omv-proton-manifest-') as temporary:
        cli.download(folder + '/' + name, temporary)
        path = Path(temporary) / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise BackupError('Invalid downloaded manifest')
        return json.loads(path.read_text())


def upload_pair(cli, config, item, archive, folder):
    manifest = archive.with_name(archive.name + '.manifest.json')
    value = metadata(json.loads(manifest.read_text()), config, item, archive.name)
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
            return value
        # A previous transfer may have completed just before interruption.
        # Size alone cannot identify an uncommitted archive with this name.
        with tempfile.TemporaryDirectory(prefix='omv-proton-retry-') as directory:
            check_space(directory, value['size'], config['minimumfreebytes'])
            cli.download(folder + '/' + archive.name, directory)
            if digest(Path(directory) / archive.name) != value['sha256']:
                raise BackupError('Uncommitted remote archive has different content')
    else:
        cli.upload(archive, folder)
    entry = unique(cli.list(folder), archive.name)
    if entry['size'] != value['size']:
        raise BackupError('Remote upload size could not be verified')
    cli.upload(manifest, folder)
    unique(cli.list(folder), manifest.name)
    if read_remote_manifest(cli, folder, manifest.name) != value:
        raise BackupError('Remote completion manifest could not be verified')
    return value


def prune_remote(cli, config, item, folder):
    listing = cli.list(folder)
    backups = []
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
        cli.purge(folder, archive)
        cli.purge(folder, manifest)
    return len(backups)
