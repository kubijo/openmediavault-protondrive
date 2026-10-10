"""Decode records received from the service without trusting JSON annotations."""

from .json_data import boolean, integer, object_value, string, validate
from .models import BackendStatus, Manifest, RemoteEntry, ServiceStatus


def remote_entry(value: object) -> RemoteEntry:
    data = object_value(value)
    kind = string(data['type'])
    if kind not in ('file', 'folder'):
        raise ValueError('Invalid remote entry type')
    size = data.get('size')
    return RemoteEntry(
        name=string(data['name']),
        type=kind,
        uid=string(data['uid']),
        size=None if size is None else integer(size),
    )


def listing(value: object) -> list[RemoteEntry]:
    data = validate(value)
    if not isinstance(data, list):
        raise TypeError('Expected a remote listing')
    return [remote_entry(entry) for entry in data]


def manifest(value: object) -> Manifest:
    from .compose import manifest_projects

    data = object_value(value)
    version = integer(data['format'])
    if version not in (1, 2):
        raise ValueError('Unsupported manifest format')
    result = Manifest(
        format=2 if version == 2 else 1,
        instanceuuid=string(data['instanceuuid']),
        setuuid=string(data['setuuid']),
        archive=string(data['archive']),
        timestamp=string(data['timestamp']),
        size=integer(data['size']),
        sha256=string(data['sha256']),
    )
    if version == 2:
        result['compose'] = manifest_projects(data['compose'])
    return result


def service_status(value: object) -> ServiceStatus:
    data = object_value(value)
    return ServiceStatus(
        state=string(data['state']),
        url=string(data['url']),
        error=string(data['error']),
        email=string(data.get('email', '')),
        organization=string(data.get('organization', '')),
        transferphase=string(data.get('transferphase', '')),
        transferfile=string(data.get('transferfile', '')),
        transferelapsed=integer(data.get('transferelapsed', 0)),
    )


def backend_statuses(value: object) -> list[BackendStatus]:
    data = validate(value)
    if not isinstance(data, list):
        raise TypeError('Expected backend statuses')
    result: list[BackendStatus] = []
    for entry in data:
        item = object_value(entry)
        state = string(item['state'])
        if state not in ('unknown', 'signed-out', 'signed-in', 'signing-in', 'error', 'unavailable'):
            raise ValueError('Invalid backend authentication state')
        status = BackendStatus(
            id=string(item['id']),
            kind=string(item['kind']),
            name=string(item['name']),
            enable=boolean(item['enable']),
            state=state,
            url=string(item['url']),
            error=string(item['error']),
            email=string(item['email']),
            organization=string(item['organization']),
            transferphase=string(item['transferphase']),
            transferfile=string(item['transferfile']),
            transferelapsed=integer(item['transferelapsed']),
        )
        if 'transferpercent' in item:
            percent = integer(item['transferpercent'])
            if not 0 <= percent <= 100:
                raise ValueError('Invalid transfer percentage')
            status['transferpercent'] = percent
        result.append(status)
    return result
