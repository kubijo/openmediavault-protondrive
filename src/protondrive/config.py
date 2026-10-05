"""Configuration validation shared by the runner and the OMV save operation."""

import re
import uuid
from pathlib import Path, PurePosixPath

from .common import CONFIG, BackupError
from .json_data import decode, object_value, string
from .models import BackupSet, Configuration


def boolean(value: object) -> bool:
    if value in (True, 1, '1', 'true'):
        return True
    if value in (False, 0, '0', 'false'):
        return False
    raise BackupError('Invalid boolean')


def lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def identifier(value: object) -> str:
    try:
        if not isinstance(value, str):
            raise TypeError()
        if str(uuid.UUID(value)) != value or uuid.UUID(value).version != 4:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise BackupError('Invalid UUID') from exc
    return value


def local_path(value: object) -> Path:
    if not isinstance(value, str) or not value.startswith('/'):
        raise BackupError('Source and staging paths must be absolute')
    if any(ord(c) < 32 for c in value) or '..' in PurePosixPath(value).parts:
        raise BackupError('Invalid path')
    return Path(value).resolve()


def bounded(value: object, name: str, low: int, high: int) -> int:
    if not isinstance(value, (str, int, float)):
        raise BackupError(f'Invalid {name}')
    result = int(value)
    if not low <= result <= high:
        raise BackupError(f'Invalid {name}')
    return result


def validate(value: object) -> Configuration:
    config = object_value(value)
    result: Configuration = {
        'enable': boolean(config['enable']),
        'instanceuuid': identifier(config['instanceuuid']),
        'schedulehour': bounded(config['schedulehour'], 'schedulehour', 0, 23),
        'scheduleminute': bounded(config['scheduleminute'], 'scheduleminute', 0, 59),
        'minimumfreebytes': bounded(config['minimumfreebytes'], 'minimumfreebytes', 1048576, 2**53 - 1),
        'containerstoptimeout': bounded(config['containerstoptimeout'], 'containerstoptimeout', 1, 600),
        'commandtimeout': bounded(config['commandtimeout'], 'commandtimeout', 1, 600),
        'transfertimeout': bounded(config['transfertimeout'], 'transfertimeout', 60, 86400),
        'stagingpath': string(config['stagingpath']),
        'remotepath': string(config['remotepath']),
        'sets': [],
    }
    staging = local_path(config['stagingpath'])
    if staging == Path('/') or staging.is_symlink():
        raise BackupError('Invalid staging directory')
    result['stagingpath'] = str(staging)
    remote = result['remotepath']
    match = re.fullmatch(r'/my-files/([A-Za-z0-9 _.-]+)', remote)
    if not match or match[1] in ('.', '..') or match[1].startswith('-'):
        raise BackupError('Remote path must be one folder directly under /my-files')
    names: set[str] = set()
    ids: set[str] = set()
    raw_sets = config.get('sets', [])
    if not isinstance(raw_sets, list):
        raise BackupError('Backup sets must be a list')
    for raw in raw_sets:
        data = object_value(raw)
        item: BackupSet = {
            'uuid': identifier(data['uuid']),
            'name': string(data['name']),
            'enable': boolean(data['enable']),
            'stopcontainers': boolean(data['stopcontainers']),
            'localkeep': bounded(data['localkeep'], 'local retention', 1, 10000),
            'remotekeep': bounded(data['remotekeep'], 'remote retention', 1, 10000),
            'paths': string(data['paths']),
            'excludes': string(data['excludes']),
        }
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', item['name']):
            raise BackupError('Set name must be a simple unique identifier')
        if item['name'] in names or item['uuid'] in ids:
            raise BackupError('Duplicate backup set')
        names.add(item['name'])
        ids.add(item['uuid'])
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
        for exclusion in lines(item['excludes']):
            if (
                exclusion.startswith('/')
                or any(p in ('.', '..') for p in exclusion.split('/'))
                or any(ord(c) < 32 for c in exclusion)
            ):
                raise BackupError('Exclusions must be literal source-relative paths')
        result['sets'].append(item)
    return result


def load() -> Configuration:
    return validate(decode(CONFIG.read_text()))


def remote_folder(config: Configuration, item: BackupSet) -> str:
    return f'{config["remotepath"]}/{config["instanceuuid"]}/{item["uuid"]}'
