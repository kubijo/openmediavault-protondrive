"""Validated records shared across the runner and the Proton service."""

from typing import Literal, NotRequired, TypedDict


class BackupSet(TypedDict):
    uuid: str
    name: str
    enable: bool
    paths: str
    excludes: str
    stopcontainers: bool
    containerids: NotRequired[str]
    composeprojects: NotRequired[str]
    localkeep: int
    remotekeep: int


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
    sets: list[BackupSet]


class RemoteEntry(TypedDict):
    name: str
    type: Literal['file', 'folder']
    uid: str
    size: int | None


class Manifest(TypedDict):
    format: Literal[1]
    instanceuuid: str
    setuuid: str
    archive: str
    timestamp: str
    size: int
    sha256: str


class TransferStatus(TypedDict):
    transferphase: str
    transferfile: str
    transferelapsed: int


class ServiceStatus(TypedDict):
    state: str
    url: str
    error: str
    email: str
    organization: str
    transferphase: str
    transferfile: str
    transferelapsed: int
