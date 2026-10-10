"""Validated records shared across the runner and the Proton service."""

from typing import Literal, NotRequired, TypedDict


class ComposeApplication(TypedDict):
    project: str
    definitions: list[str]
    envfiles: list[str]
    secretfiles: list[str]


class ComposeBindManifest(TypedDict):
    source: str
    target: str
    read_only: bool


class ComposeServiceManifest(TypedDict):
    service: str
    image: str
    pinned: str
    replicas: int
    binds: list[ComposeBindManifest]


class ComposeProjectManifest(ComposeApplication):
    services: list[ComposeServiceManifest]


class BackupSet(TypedDict):
    uuid: str
    name: str
    enable: bool
    paths: str
    excludes: str
    stopcontainers: bool
    containerids: NotRequired[str]
    composeprojects: NotRequired[str]
    composeapps: NotRequired[list[ComposeApplication]]
    localkeep: int
    remotekeep: int


class Destination(TypedDict):
    id: str
    kind: str
    name: str
    enable: bool
    root: str


class Configuration(TypedDict):
    enable: bool
    instanceuuid: str
    schedulehour: int
    scheduleminute: int
    minimumfreebytes: int
    containerstoptimeout: int
    commandtimeout: int
    transfertimeout: int
    stagingpath: str
    remotepath: str
    destinations: list[Destination]
    sets: list[BackupSet]


class RemoteEntry(TypedDict):
    name: str
    type: Literal['file', 'folder']
    uid: str
    size: int | None


class Manifest(TypedDict):
    format: Literal[1, 2]
    instanceuuid: str
    setuuid: str
    archive: str
    timestamp: str
    size: int
    sha256: str
    compose: NotRequired[list[ComposeProjectManifest]]


class TransferStatus(TypedDict):
    transferphase: str
    transferfile: str
    transferelapsed: int
    transferpercent: NotRequired[int]


AuthState = Literal['unknown', 'signed-out', 'signed-in', 'signing-in', 'error', 'unavailable']


class AuthStatus(TypedDict):
    state: AuthState
    url: str
    error: str
    email: str
    organization: str


class RecoveryRecord(TypedDict):
    ids: list[str]
    stop_deadline: NotRequired[float]


def auth_status(
    state: AuthState, *, url: str = '', error: str = '', email: str = '', organization: str = ''
) -> AuthStatus:
    return AuthStatus(state=state, url=url, error=error, email=email, organization=organization)


class ServiceStatus(TypedDict):
    state: str
    url: str
    error: str
    email: str
    organization: str
    transferphase: str
    transferfile: str
    transferelapsed: int


class BackendStatus(AuthStatus, TransferStatus):
    id: str
    kind: str
    name: str
    enable: bool
