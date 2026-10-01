"""Configuration validation shared by the runner and the OMV save operation."""

import json
import re
import uuid
from pathlib import Path, PurePosixPath

from .common import CONFIG, BackupError


def boolean(value):
    if value in (True, 1, '1', 'true'):
        return True
    if value in (False, 0, '0', 'false'):
        return False
    raise BackupError('Invalid boolean')


def lines(value):
    return [line.strip() for line in value.splitlines() if line.strip()]


def identifier(value):
    try:
        if str(uuid.UUID(value)) != value or uuid.UUID(value).version != 4:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise BackupError('Invalid UUID') from exc
    return value


def local_path(value):
    if not isinstance(value, str) or not value.startswith('/'):
        raise BackupError('Source and staging paths must be absolute')
    if any(ord(c) < 32 for c in value) or '..' in PurePosixPath(value).parts:
        raise BackupError('Invalid path')
    return Path(value).resolve()


def validate(config):
    result = dict(config)
    result['enable'] = boolean(config['enable'])
    identifier(config['instanceuuid'])
    for key, low, high in (
        ('schedulehour', 0, 23),
        ('scheduleminute', 0, 59),
        ('minimumfreebytes', 1048576, 2**53 - 1),
        ('containerstoptimeout', 1, 600),
        ('commandtimeout', 1, 600),
        ('transfertimeout', 60, 86400),
    ):
        value = int(config[key])
        if not low <= value <= high:
            raise BackupError(f'Invalid {key}')
        result[key] = value
    staging = local_path(config['stagingpath'])
    if staging == Path('/') or staging.is_symlink():
        raise BackupError('Invalid staging directory')
    result['stagingpath'] = str(staging)
    remote = config['remotepath']
    if not re.fullmatch(r'/my-files(?:/[A-Za-z0-9 _.-]+)+', remote):
        raise BackupError('Remote path must be a folder below /my-files')
    if any(p in ('.', '..') or p.startswith('-') for p in remote.split('/')[2:]):
        raise BackupError('Invalid remote path component')
    names, ids, sets = set(), set(), []
    for item in config.get('sets', []):
        item = dict(item)
        identifier(item['uuid'])
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', item['name']):
            raise BackupError('Set name must be a simple unique identifier')
        if item['name'] in names or item['uuid'] in ids:
            raise BackupError('Duplicate backup set')
        names.add(item['name'])
        ids.add(item['uuid'])
        for key in ('enable', 'stopcontainers'):
            item[key] = boolean(item[key])
        for key in ('localkeep', 'remotekeep'):
            item[key] = int(item[key])
            if not 1 <= item[key] <= 10000:
                raise BackupError('Retention must be between 1 and 10000')
        paths = lines(item['paths'])
        if not paths:
            raise BackupError('A backup set needs at least one source')
        resolved = [local_path(p) for p in paths]
        for path in resolved:
            if path == staging or path in staging.parents or staging in path.parents:
                raise BackupError('Source and staging paths must not overlap')
        for index, path in enumerate(resolved):
            if any(path == other or path in other.parents or other in path.parents for other in resolved[:index]):
                raise BackupError('Sources within a set must not overlap')
        for value in lines(item['excludes']):
            if (
                value.startswith('/')
                or any(p in ('.', '..') for p in value.split('/'))
                or any(ord(c) < 32 for c in value)
            ):
                raise BackupError('Exclusions must be literal source-relative paths')
        sets.append(item)
    result['sets'] = sets
    return result


def load():
    with CONFIG.open() as stream:
        return validate(json.load(stream))


def remote_folder(config, item):
    return f'{config["remotepath"]}/{config["instanceuuid"]}/{item["uuid"]}'
