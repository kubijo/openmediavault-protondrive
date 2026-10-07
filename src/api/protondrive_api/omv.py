"""OMV adapter: fixed RPCs and existing supervised backup entry points."""

import hashlib
import json
import subprocess
import time
import uuid
from collections.abc import Callable

from protondrive.json_data import JSONValue, boolean, decode, integer, object_value, string

from .v1 import control_pb2 as wire


def execute(arguments: list[str], timeout: float = 30) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        # OMV may include configuration or credentials in exception traces.
        # Keep those out of public RPC errors.
        raise RuntimeError(f'{arguments[0]} failed (exit {result.returncode}); inspect the service journal')
    return result.stdout


def revision(value: JSONValue) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def new_object_id() -> str:
    key, separator, value = execute(['/usr/sbin/omv-env', 'get', 'OMV_CONFIGOBJECT_NEW_UUID']).strip().partition('=')
    if key != 'OMV_CONFIGOBJECT_NEW_UUID' or separator != '=':
        raise ValueError('OMV did not return its new-object identifier')
    return str(uuid.UUID(value))


def configuration(value: dict[str, JSONValue]) -> wire.Configuration:
    return wire.Configuration(
        enabled=boolean(value['enable']),
        instance_id=string(value['instanceuuid']),
        schedule_hour=integer(value['schedulehour']),
        schedule_minute=integer(value['scheduleminute']),
        minimum_free_bytes=integer(value['minimumfreebytes']),
        container_stop_timeout_seconds=integer(value['containerstoptimeout']),
        command_timeout_seconds=integer(value['commandtimeout']),
        transfer_timeout_seconds=integer(value['transfertimeout']),
        staging_path=string(value['stagingpath']),
        remote_path=string(value['remotepath']),
    )


def configuration_json(value: wire.Configuration) -> dict[str, JSONValue]:
    return {
        'enable': value.enabled,
        'instanceuuid': value.instance_id,
        'schedulehour': value.schedule_hour,
        'scheduleminute': value.schedule_minute,
        'minimumfreebytes': value.minimum_free_bytes,
        'containerstoptimeout': value.container_stop_timeout_seconds,
        'commandtimeout': value.command_timeout_seconds,
        'transfertimeout': value.transfer_timeout_seconds,
        'stagingpath': value.staging_path,
        'remotepath': value.remote_path,
    }


def backup_set(value: dict[str, JSONValue]) -> wire.BackupSet:
    return wire.BackupSet(
        id=string(value['uuid']),
        name=string(value['name']),
        enabled=boolean(value['enable']),
        paths=string(value['paths']).splitlines(),
        exclusions=string(value['excludes']).splitlines(),
        stop_all_containers=boolean(value['stopcontainers']),
        container_ids=string(value.get('containerids', '')).splitlines(),
        compose_projects=string(value.get('composeprojects', '')).splitlines(),
        local_keep=integer(value['localkeep']),
        remote_keep=integer(value['remotekeep']),
    )


class OMV:
    def rpc(self, actor: str, service: str, method: str, parameters: dict[str, JSONValue]) -> JSONValue:
        value = decode(execute(['/usr/sbin/omv-rpc', '-u', actor, service, method, json.dumps(parameters)]))
        if isinstance(value, dict) and 'response' in value and isinstance(value.get('error'), dict):
            raise RuntimeError(f'OMV {service}.{method} failed; inspect the service journal')
        return value

    def get_configuration(self, actor: str) -> wire.GetConfigurationResponse:
        value = object_value(self.rpc(actor, 'ProtonDrive', 'get', {}))
        return wire.GetConfigurationResponse(configuration=configuration(value), revision=revision(value))

    def save_configuration(self, actor: str, request: wire.SaveConfigurationRequest) -> wire.SaveConfigurationResponse:
        if self.get_configuration(actor).revision != request.revision:
            raise ValueError('Configuration changed; reload before saving')
        result = self.rpc(actor, 'ProtonDrive', 'set', configuration_json(request.configuration))
        return wire.SaveConfigurationResponse(revision=revision(result))

    def list_sets(self, actor: str) -> wire.ListSetsResponse:
        result = object_value(
            self.rpc(
                actor,
                'ProtonDrive',
                'getSetList',
                {
                    'start': 0,
                    'limit': 10000,
                    'sortfield': 'name',
                    'sortdir': 'ASC',
                },
            )
        )
        data = result['data']
        if not isinstance(data, list):
            raise TypeError('OMV returned an invalid backup-set list')
        return wire.ListSetsResponse(sets=[backup_set(object_value(item)) for item in data], revision=revision(data))

    def save_set(self, actor: str, request: wire.SaveSetRequest) -> wire.SaveSetResponse:
        if self.list_sets(actor).revision != request.revision:
            raise ValueError('Backup sets changed; reload before saving')
        value = request.set
        result = self.rpc(
            actor,
            'ProtonDrive',
            'setSet',
            {
                'uuid': value.id or new_object_id(),
                'name': value.name,
                'enable': value.enabled,
                'paths': '\n'.join(value.paths),
                'excludes': '\n'.join(value.exclusions),
                'stopcontainers': value.stop_all_containers,
                'containerids': '\n'.join(value.container_ids),
                'composeprojects': '\n'.join(value.compose_projects),
                'localkeep': value.local_keep,
                'remotekeep': value.remote_keep,
            },
        )
        return wire.SaveSetResponse(set=backup_set(object_value(result)))

    def delete_set(self, actor: str, request: wire.DeleteSetRequest) -> wire.DeleteSetResponse:
        if self.list_sets(actor).revision != request.revision:
            raise ValueError('Backup sets changed; reload before deleting')
        self.rpc(actor, 'ProtonDrive', 'deleteSet', {'uuid': request.id})
        return wire.DeleteSetResponse()

    def status(self, actor: str) -> wire.GetStatusResponse:
        value = object_value(self.rpc(actor, 'ProtonDrive', 'getStatus', {}))
        dirty = self.rpc(actor, 'Config', 'isDirty', {'modules': ['protondrive']})
        return wire.GetStatusResponse(
            phase=string(value['phase']),
            running=boolean(value['running']),
            recovery_pending=boolean(value['recoverypending']),
            last_success=string(value['lastsuccess']),
            error=string(value['error']),
            account_state=string(value['authstate']),
            account_error=string(value['autherror']),
            account_email=string(value['accountemail']),
            authentication_url=string(value['authurl']),
            transfer_phase=string(value['transferphase']),
            transfer_file=string(value['transferfile']),
            transfer_elapsed_seconds=integer(value['transferelapsed']),
            pending_configuration=boolean(dirty),
        )

    def browse(self, actor: str, request: wire.BrowseDirectoriesRequest) -> wire.BrowseDirectoriesResponse:
        data = self.rpc(
            actor,
            'FolderBrowser',
            'get',
            {
                'uuid': request.shared_folder_id,
                'type': 'sharedfolder',
                'path': request.relative_path,
            },
        )
        if not isinstance(data, list):
            raise TypeError('OMV returned an invalid directory list')
        directories: list[wire.Directory] = []
        for value in data:
            name = string(value)
            relative = '/'.join(part for part in (request.relative_path.rstrip('/'), name) if part)
            directories.append(wire.Directory(name=name, relative_path=relative))
        return wire.BrowseDirectoriesResponse(directories=directories)

    def operation(self, actor: str, operation: wire.Operation.ValueType, progress: Callable[[str], None]) -> None:
        commands = {
            wire.OPERATION_CANCEL_BACKUP: 'cancel-run',
            wire.OPERATION_RECOVER_CONTAINERS: 'recover',
            wire.OPERATION_START_AUTHENTICATION: 'start-auth',
            wire.OPERATION_CANCEL_AUTHENTICATION: 'cancel-auth',
            wire.OPERATION_SIGN_OUT: 'logout',
        }
        if operation in commands:
            execute(['/usr/sbin/omv-protondrive', commands[operation]], 700)
        elif operation == wire.OPERATION_APPLY_CONFIGURATION:
            result = self.rpc(actor, 'Config', 'applyChangesBg', {'modules': ['protondrive'], 'force': False})
            if not isinstance(result, str):
                raise ValueError('OMV did not return a configuration task')
            self._follow_task(actor, result, progress)
        elif operation == wire.OPERATION_BACKUP:
            if self.status(actor).pending_configuration:
                raise ValueError('Apply pending configuration before starting a backup')
            result = self.rpc(actor, 'ProtonDrive', 'runNow', {})
            if not isinstance(result, str):
                raise ValueError('OMV did not return a backup task')
            self._follow_task(actor, result, progress)
            status = self.status(actor)
            if status.phase != 'completed':
                raise RuntimeError('Backup did not complete; inspect backup status')
        else:
            raise ValueError('Unsupported operation')

    def _follow_task(self, actor: str, filename: str, progress: Callable[[str], None]) -> None:
        deadline = time.monotonic() + 90000
        position = 0
        while time.monotonic() < deadline:
            value = object_value(self.rpc(actor, 'Exec', 'getOutput', {'filename': filename, 'pos': position}))
            position = integer(value['pos'])
            if not boolean(value['running']):
                if value.get('exitcode', 0) != 0:
                    raise RuntimeError('OMV operation failed; inspect the service journal')
                return
            progress('Operation running')
            time.sleep(1)
        raise TimeoutError('Timed out observing OMV operation; inspect status before retrying')
