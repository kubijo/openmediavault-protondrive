"""Resolve explicit container scopes without touching unrelated workloads."""

import re
from dataclasses import dataclass

from .common import BackupError
from .config import lines
from .json_data import boolean, decode, object_value, string
from .models import BackupSet
from .recovery import DockerCommand, docker


@dataclass(frozen=True)
class Container:
    identifier: str
    name: str
    project: str
    running: bool


def inventory(command: DockerCommand = docker) -> list[Container]:
    ids = command('ps', '--all', '--quiet', '--no-trunc').split()
    if any(not re.fullmatch(r'[a-f0-9]{64}', identifier) for identifier in ids):
        raise BackupError('Invalid Docker container inventory')
    result: list[Container] = []
    for identifier in ids:
        data = decode(command('inspect', identifier))
        if not isinstance(data, list) or len(data) != 1:
            raise BackupError('Invalid Docker inspection response')
        value = object_value(data[0])
        labels_value = object_value(value['Config']).get('Labels')
        labels = {} if labels_value is None else object_value(labels_value)
        result.append(
            Container(
                identifier,
                string(value['Name']).removeprefix('/'),
                string(labels.get('com.docker.compose.project', '')),
                boolean(object_value(value['State'])['Running']),
            )
        )
    return result


def selection(item: BackupSet, command: DockerCommand = docker) -> tuple[str, ...] | None:
    """None preserves legacy stop-all; an empty tuple means no containers."""
    identifiers = lines(item.get('containerids', ''))
    projects = lines(item.get('composeprojects', ''))
    if item['stopcontainers']:
        if identifiers or projects:
            raise BackupError('Choose either all containers or an explicit application scope')
        return None
    if not identifiers and not projects:
        return ()
    available = command('ps', '--all', '--quiet', '--no-trunc').split()
    if any(not re.fullmatch(r'[a-f0-9]{64}', identifier) for identifier in available):
        raise BackupError('Invalid Docker container inventory')
    if any(identifier not in available for identifier in identifiers):
        raise BackupError('A selected container no longer exists; update the backup set')
    selected = set(identifiers)
    found: set[str] = set()
    for identifier in available:
        value = decode(command('inspect', '--format', '{{json .Config.Labels}}', identifier))
        labels = {} if value is None else object_value(value)
        project = string(labels.get('com.docker.compose.project', ''))
        if project in projects:
            selected.add(identifier)
            found.add(project)
    if set(projects) != found:
        raise BackupError('A selected Compose project has no containers; update the backup set')
    return tuple(sorted(selected))
