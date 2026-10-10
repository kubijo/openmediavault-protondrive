"""Configuration validation shared by the runner and the OMV save operation."""

import re
import uuid
from pathlib import Path, PurePosixPath
from typing import cast

from .common import CONFIG, BackupError
from .json_data import JSONValue, decode, object_value, string
from .models import BackupSet, ComposeApplication, Configuration, Destination


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


def application_files(value: JSONValue, label: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 64 or (required and not value):
        raise BackupError(f'Invalid Compose {label} selection')
    paths: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or len(entry.encode()) > 4096:
            raise BackupError(f'Invalid Compose {label} path')
        path = local_path(entry)
        if path == Path('/'):
            raise BackupError(f'Invalid Compose {label} path')
        paths.append(str(path))
    if len(paths) != len(set(paths)):
        raise BackupError(f'Duplicate Compose {label} path')
    return paths


def applications(value: object) -> list[ComposeApplication]:
    if value == '':
        return []
    if not isinstance(value, str) or len(value.encode()) > 65536:
        raise BackupError('Invalid Compose application selection')
    data = decode(value.removeprefix('json:'))
    if not isinstance(data, list) or len(data) > 32:
        raise BackupError('Invalid Compose application selection')
    result: list[ComposeApplication] = []
    for raw in data:
        entry = object_value(raw)
        if set(entry) != {'project', 'definitions', 'envfiles', 'secretfiles'}:
            raise BackupError('Invalid Compose application fields')
        project = string(entry['project'])
        if re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project) is None:
            raise BackupError('Invalid Compose project name')
        definitions = application_files(entry['definitions'], 'definition', required=True)
        envfiles = application_files(entry['envfiles'], 'environment')
        secretfiles = application_files(entry['secretfiles'], 'secret')
        paths = definitions + envfiles + secretfiles
        if len(paths) != len(set(paths)):
            raise BackupError('Compose file selected in multiple roles')
        result.append(
            ComposeApplication(project=project, definitions=definitions, envfiles=envfiles, secretfiles=secretfiles)
        )
    if len({entry['project'] for entry in result}) != len(result):
        raise BackupError('Duplicate Compose application')
    return result


def destinations(value: object, legacy_root: str) -> list[Destination]:
    if value is None or value == '':
        return [Destination(id='protondrive', kind='protondrive', name='Proton Drive', enable=True, root=legacy_root)]
    if isinstance(value, str):
        if len(value.encode()) > 65536:
            raise BackupError('Invalid backup destinations')
        raw = decode(value.removeprefix('json:'))
    else:
        raw = value
    if not isinstance(raw, list):
        raise BackupError('A backup needs between one and 32 destinations')
    members = cast('list[JSONValue]', raw)
    if not 0 < len(members) <= 32:
        raise BackupError('A backup needs between one and 32 destinations')
    result: list[Destination] = []
    for member in members:
        entry = object_value(member)
        if set(entry) != {'id', 'kind', 'name', 'enable', 'root'}:
            raise BackupError('Invalid backup destination fields')
        identifier = string(entry['id'])
        kind = string(entry['kind'])
        name = string(entry['name'])
        root = string(entry['root'])
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', identifier) is None or len(name) > 80 or not name:
            raise BackupError('Invalid backup destination identity')
        if kind != 'protondrive':
            raise BackupError(f'Unsupported storage backend: {kind}')
        if re.fullmatch(r'/my-files/([A-Za-z0-9 _.-]+)', root) is None:
            raise BackupError('Invalid Proton Drive destination root')
        result.append(Destination(id=identifier, kind=kind, name=name, enable=boolean(entry['enable']), root=root))
    if len({entry['id'] for entry in result}) != len(result):
        raise BackupError('Duplicate backup destination')
    if len({entry['kind'] for entry in result}) != len(result):
        raise BackupError('A backend can have only one account until its sessions are isolated')
    if not any(entry['enable'] for entry in result):
        raise BackupError('At least one backup destination must be enabled')
    return result


def validate(value: object) -> Configuration:
    config = object_value(value)
    result = Configuration(
        enable=boolean(config['enable']),
        instanceuuid=identifier(config['instanceuuid']),
        schedulehour=bounded(config['schedulehour'], 'schedulehour', 0, 23),
        scheduleminute=bounded(config['scheduleminute'], 'scheduleminute', 0, 59),
        minimumfreebytes=bounded(config['minimumfreebytes'], 'minimumfreebytes', 1048576, 2**53 - 1),
        containerstoptimeout=bounded(config['containerstoptimeout'], 'containerstoptimeout', 1, 600),
        commandtimeout=bounded(config['commandtimeout'], 'commandtimeout', 1, 600),
        transfertimeout=bounded(config['transfertimeout'], 'transfertimeout', 60, 86400),
        stagingpath=string(config['stagingpath']),
        remotepath=string(config['remotepath']),
        destinations=[],
        sets=[],
    )
    staging = local_path(config['stagingpath'])
    if staging == Path('/') or staging.is_symlink():
        raise BackupError('Invalid staging directory')
    result['stagingpath'] = str(staging)
    remote = result['remotepath']
    match = re.fullmatch(r'/my-files/([A-Za-z0-9 _.-]+)', remote)
    if not match or match[1] in ('.', '..') or match[1].startswith('-'):
        raise BackupError('Remote path must be one folder directly under /my-files')
    result['destinations'] = destinations(config.get('destinations', ''), remote)
    names: set[str] = set()
    ids: set[str] = set()
    raw_sets = config.get('sets', [])
    if not isinstance(raw_sets, list):
        raise BackupError('Backup sets must be a list')
    for raw in raw_sets:
        data = object_value(raw)
        item = BackupSet(
            uuid=identifier(data['uuid']),
            name=string(data['name']),
            enable=boolean(data['enable']),
            stopcontainers=boolean(data['stopcontainers']),
            localkeep=bounded(data['localkeep'], 'local retention', 1, 10000),
            remotekeep=bounded(data['remotekeep'], 'remote retention', 1, 10000),
            paths=string(data['paths']),
            excludes=string(data['excludes']),
        )
        selected_containers = string(data.get('containerids', ''))
        selected_projects = string(data.get('composeprojects', ''))
        if any(not re.fullmatch(r'[a-f0-9]{64}', cid) for cid in lines(selected_containers)):
            raise BackupError('Selected containers must be full Docker identifiers')
        if any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name) for name in lines(selected_projects)):
            raise BackupError('Invalid Compose project name')
        if len(lines(selected_projects)) != len(set(lines(selected_projects))):
            raise BackupError('Duplicate Compose project')
        compose_apps = applications(data.get('composeapps', ''))
        if compose_apps:
            if any(app['project'] not in lines(selected_projects) for app in compose_apps):
                raise BackupError('Compose application must be a selected project')
            item['composeapps'] = compose_apps
        if selected_containers or selected_projects:
            if item['stopcontainers']:
                raise BackupError('Choose either all containers or an explicit application scope')
            item['containerids'] = selected_containers
            item['composeprojects'] = selected_projects
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
        for app in compose_apps:
            for selected_file in app['definitions'] + app['envfiles'] + app['secretfiles']:
                path = Path(selected_file)
                if not any(path == source or source in path.parents for source in resolved):
                    raise BackupError(f'Compose file is not selected for backup: {path}')
                if any(
                    path == source / exclusion or source / exclusion in path.parents
                    for source in resolved
                    for exclusion in lines(item['excludes'])
                ):
                    raise BackupError(f'Compose file is excluded from the archive: {path}')
        result['sets'].append(item)
    return result


def load() -> Configuration:
    return validate(decode(CONFIG.read_text()))


def remote_folder(config: Configuration, item: BackupSet, destination: Destination | None = None) -> str:
    root = config['remotepath'] if destination is None else destination['root']
    return f'{root}/{config["instanceuuid"]}/{item["uuid"]}'
