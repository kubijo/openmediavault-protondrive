"""Decode records received from the service without trusting JSON annotations."""

from .json_data import integer, object_value, string, validate
from .models import Manifest, RemoteEntry, ServiceStatus


def remote_entry(value: object) -> RemoteEntry:
    data = object_value(value)
    kind = string(data['type'])
    if kind not in ('file', 'folder'):
        raise ValueError('Invalid remote entry type')
    size = data.get('size')
    return {
        'name': string(data['name']),
        'type': kind,
        'uid': string(data['uid']),
        'size': None if size is None else integer(size),
    }


def listing(value: object) -> list[RemoteEntry]:
    data = validate(value)
    if not isinstance(data, list):
        raise TypeError('Expected a remote listing')
    return [remote_entry(entry) for entry in data]


def manifest(value: object) -> Manifest:
    data = object_value(value)
    if integer(data['format']) != 1:
        raise ValueError('Unsupported manifest format')
    return {
        'format': 1,
        'instanceuuid': string(data['instanceuuid']),
        'setuuid': string(data['setuuid']),
        'archive': string(data['archive']),
        'timestamp': string(data['timestamp']),
        'size': integer(data['size']),
        'sha256': string(data['sha256']),
    }


def service_status(value: object) -> ServiceStatus:
    data = object_value(value)
    return {
        'state': string(data['state']),
        'url': string(data['url']),
        'error': string(data['error']),
        'email': string(data.get('email', '')),
        'organization': string(data.get('organization', '')),
        'transferphase': string(data.get('transferphase', '')),
        'transferfile': string(data.get('transferfile', '')),
        'transferelapsed': integer(data.get('transferelapsed', 0)),
    }
