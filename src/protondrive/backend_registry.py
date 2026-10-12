"""Construct the configured storage adapter at the service boundary."""

from .backend import StorageBackend
from .common import BackupError
from .models import Configuration, Destination
from .protoncli import ProtonCli, load_owner_id


def create_backend(destination: Destination, config: Configuration) -> StorageBackend:
    if destination['kind'] == 'protondrive':
        scoped = config.copy()
        scoped['remotepath'] = destination['root']
        return ProtonCli(scoped, owner_id=load_owner_id())
    raise BackupError(f'Unsupported storage backend: {destination["kind"]}')
