"""Typed boundaries for standalone disposable guest checks."""

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Literal, TypedDict, TypeGuard, Unpack, cast, overload


def debian_package_name(package: Path) -> str:
    result = subprocess.run(
        ['dpkg-deb', '--field', str(package), 'Package'],
        check=True,
        capture_output=True,
        text=True,
    )
    name = result.stdout.strip()
    if re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', name) is None:
        raise ValueError('Invalid Debian package name')
    return name


def is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def mapping(value: object) -> dict[str, object]:
    if not is_mapping(value):
        raise TypeError('Expected an object')
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError('Expected string keys')
        result[key] = item
    return result


def decode(text: str | bytes) -> dict[str, object]:
    return mapping(decode_value(text))


def decode_value(text: str | bytes) -> object:
    return cast(object, json.loads(text))


def is_list(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, list)


def items(value: object) -> list[object]:
    if not is_list(value):
        raise TypeError('Expected a list')
    return list(value)


def string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError('Expected a string')
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError('Expected an integer')
    return value


def boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError('Expected a boolean')
    return value


class OmvConfiguration(TypedDict):
    enable: bool
    schedulehour: int
    scheduleminute: int
    stagingpath: str
    remotepath: str
    destinations: str
    instanceuuid: str
    minimumfreebytes: int
    containerstoptimeout: int
    commandtimeout: int
    transfertimeout: int


class OmvDestination(TypedDict):
    id: str
    kind: Literal['protondrive']
    name: str
    enable: bool
    root: str


class OmvBackupSet(TypedDict):
    uuid: str
    name: str
    enable: bool
    paths: str
    excludes: str
    stopcontainers: bool
    localkeep: int
    remotekeep: int
    containerids: str
    composeprojects: str
    composeapps: str


class InteractiveFixture(TypedDict):
    admin_password: str
    remote_folder: str


def interactive_fixture(value: object) -> InteractiveFixture:
    raw = mapping(value)
    return InteractiveFixture(admin_password=string(raw['admin_password']), remote_folder=string(raw['remote_folder']))


def omv_configuration(value: object) -> OmvConfiguration:
    raw = mapping(value)
    return OmvConfiguration(
        enable=boolean(raw['enable']),
        schedulehour=integer(raw['schedulehour']),
        scheduleminute=integer(raw['scheduleminute']),
        stagingpath=string(raw['stagingpath']),
        remotepath=string(raw['remotepath']),
        destinations=string(raw['destinations']),
        instanceuuid=string(raw['instanceuuid']),
        minimumfreebytes=integer(raw['minimumfreebytes']),
        containerstoptimeout=integer(raw['containerstoptimeout']),
        commandtimeout=integer(raw['commandtimeout']),
        transfertimeout=integer(raw['transfertimeout']),
    )


def omv_backup_set(value: object) -> OmvBackupSet:
    raw = mapping(value)
    return OmvBackupSet(
        uuid=string(raw['uuid']),
        name=string(raw['name']),
        enable=boolean(raw['enable']),
        paths=string(raw['paths']),
        excludes=string(raw['excludes']),
        stopcontainers=boolean(raw['stopcontainers']),
        localkeep=integer(raw['localkeep']),
        remotekeep=integer(raw['remotekeep']),
        containerids=string(raw.get('containerids', '')),
        composeprojects=string(raw.get('composeprojects', '')),
        composeapps=string(raw.get('composeapps', '')),
    )


def field(value: object, *keys: str) -> object:
    for key in keys:
        value = mapping(value)[key]
    return value


class RunOptions(TypedDict, total=False):
    cwd: str | Path
    stdin: int | IO[bytes] | IO[str] | None
    stdout: int | IO[bytes] | IO[str] | None
    stderr: int | IO[bytes] | IO[str] | None
    capture_output: bool
    timeout: float | None
    env: Mapping[str, str] | None
    start_new_session: bool


@overload
def run(
    *args: str, text: Literal[True], input: str | None = None, check: bool = True, **kwargs: Unpack[RunOptions]
) -> subprocess.CompletedProcess[str]: ...


@overload
def run(
    *args: str,
    text: Literal[False] = False,
    input: bytes | None = None,
    check: bool = True,
    **kwargs: Unpack[RunOptions],
) -> subprocess.CompletedProcess[bytes]: ...


def run(
    *args: str, text: bool = False, input: str | bytes | None = None, check: bool = True, **kwargs: Unpack[RunOptions]
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    print('RUN', args, flush=True)
    if text:
        if input is not None and not isinstance(input, str):
            raise TypeError('Text commands require string input')
        return subprocess.run(args, text=True, input=input, check=check, **kwargs)
    if input is not None and not isinstance(input, bytes):
        raise TypeError('Binary commands require bytes input')
    return subprocess.run(args, input=input, check=check, **kwargs)


class ProtonDriveRpc:
    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose

    def _request(self, method: Literal['get', 'set', 'getSetList', 'setSet'], payload: str) -> object:
        command = ('omv-rpc', '-u', 'admin', 'ProtonDrive', method, payload)
        result = (
            run(*command, capture_output=True, text=True, check=False)
            if self.verbose
            else subprocess.run(command, capture_output=True, text=True, check=False)
        )
        if result.returncode:
            raise RuntimeError(f'RPC {method} failed: {result.stdout} {result.stderr}')
        return decode_value(result.stdout)

    def get_configuration(self) -> OmvConfiguration:
        return omv_configuration(self._request('get', '{}'))

    def save_configuration(self, value: OmvConfiguration) -> OmvConfiguration:
        return omv_configuration(self._request('set', json.dumps(value)))

    def list_sets(self) -> list[OmvBackupSet]:
        result = mapping(
            self._request('getSetList', json.dumps({'start': 0, 'limit': -1, 'sortfield': 'name', 'sortdir': 'ASC'}))
        )
        return [omv_backup_set(entry) for entry in items(result['data'])]

    def save_set(self, value: OmvBackupSet) -> OmvBackupSet:
        return omv_backup_set(self._request('setSet', json.dumps(value)))
