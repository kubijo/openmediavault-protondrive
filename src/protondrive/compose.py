"""Capture recoverable Compose service identities before archiving."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .common import BackupError
from .config import lines
from .json_data import JSONValue, boolean, decode, object_value, string
from .models import (
    BackupSet,
    ComposeApplication,
    ComposeBindManifest,
    ComposeProjectManifest,
    ComposeServiceManifest,
)
from .recovery import DockerCommand, docker

CONTAINER_ID = re.compile(r'[a-f0-9]{64}')
IMAGE_ID = re.compile(r'sha256:[a-f0-9]{64}')
DIGEST = re.compile(r'sha256:[a-f0-9]{64}')
SERVICE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')


@dataclass(frozen=True)
class BindMount:
    source: str
    target: str
    read_only: bool


@dataclass(frozen=True)
class ServicePin:
    service: str
    image: str
    pinned: str
    replicas: int
    binds: tuple[BindMount, ...]


@dataclass(frozen=True)
class ProjectCapture:
    project: str
    definitions: tuple[str, ...]
    envfiles: tuple[str, ...]
    secretfiles: tuple[str, ...]
    services: tuple[ServicePin, ...]


def repository(reference: str) -> str:
    name = reference.split('@', 1)[0]
    tail = name.rsplit('/', 1)[-1]
    if ':' in tail:
        name = name[: -len(tail)] + tail.rsplit(':', 1)[0]
    parts = name.split('/')
    if not parts or not parts[0]:
        raise BackupError('Invalid Compose image reference')
    if '.' not in parts[0] and ':' not in parts[0] and parts[0] != 'localhost':
        parts.insert(0, 'docker.io')
    if parts[0] == 'index.docker.io':
        parts[0] = 'docker.io'
    if parts[0] == 'docker.io' and len(parts) == 2:
        parts.insert(1, 'library')
    return '/'.join(parts)


def pin_image(reference: str, image_id: str, command: DockerCommand = docker) -> str:
    if IMAGE_ID.fullmatch(image_id) is None:
        raise BackupError('Docker returned an invalid image identity')
    values = decode(command('image', 'inspect', '--format', '{{json .RepoDigests}}', image_id))
    if not isinstance(values, list):
        raise BackupError(f'Compose image {reference} has no pullable registry digest')
    matches: set[str] = set()
    for value in values:
        candidate = string(value)
        name, separator, digest = candidate.partition('@')
        if separator and DIGEST.fullmatch(digest) and repository(name) == repository(reference):
            matches.add(candidate)
    if len(matches) != 1:
        raise BackupError(f'Compose image {reference} has no unique pullable registry digest')
    return matches.pop()


def mounts(identifier: str, sources: list[Path], excludes: list[str], command: DockerCommand) -> tuple[BindMount, ...]:
    values = decode(command('inspect', '--format', '{{json .Mounts}}', identifier))
    if not isinstance(values, list):
        raise BackupError('Docker returned an invalid mount list')
    result: list[BindMount] = []
    for value in values:
        mount = object_value(value)
        if mount.get('Type') != 'bind':
            raise BackupError('Selected Compose services use a named, anonymous or unsupported volume')
        path = Path(string(mount['Source']))
        try:
            direct = path.resolve(strict=True) == path
        except OSError as exc:
            raise BackupError(f'Compose bind source is missing: {path}') from exc
        if not path.is_absolute() or not direct:
            raise BackupError('Compose bind source must be an existing, direct absolute path')
        if not any(path == source or source in path.parents for source in sources):
            raise BackupError(f'Compose bind source is not selected for backup: {path}')
        if any(
            path == omitted or path in omitted.parents or omitted in path.parents
            for source in sources
            for exclusion in excludes
            for omitted in (source / exclusion,)
        ):
            raise BackupError(f'Compose bind source overlaps an archive exclusion: {path}')
        target = string(mount['Destination'])
        if not target.startswith('/') or '..' in Path(target).parts:
            raise BackupError('Docker returned an invalid bind destination')
        result.append(BindMount(str(path), target, not boolean(mount['RW'])))
    return tuple(sorted(result, key=lambda mount: (mount.source, mount.target)))


def selected_files(app: ComposeApplication, sources: list[Path]) -> None:
    for value in app['definitions'] + app['envfiles'] + app['secretfiles']:
        path = Path(value)
        try:
            direct = path.resolve(strict=True) == path
        except OSError as exc:
            raise BackupError(f'Compose file is missing: {path}') from exc
        if not path.is_file() or path.is_symlink() or not direct:
            raise BackupError(f'Compose file is not an existing direct regular file: {path}')
        if not any(path == source or source in path.parents for source in sources):
            raise BackupError(f'Compose file is not selected for backup: {path}')


def config_files(labels: Mapping[str, object]) -> tuple[str, ...]:
    raw = labels.get('com.docker.compose.project.config_files')
    working = labels.get('com.docker.compose.project.working_dir')
    if not isinstance(raw, str) or not raw or not isinstance(working, str) or not Path(working).is_absolute():
        raise BackupError('Compose project has no usable definition labels')
    try:
        return tuple(str((Path(working) / value).resolve(strict=True)) for value in raw.split(','))
    except OSError as exc:
        raise BackupError('Compose definition from Docker labels is missing') from exc


def capture(item: BackupSet, command: DockerCommand = docker) -> tuple[ProjectCapture, ...]:
    applications = item.get('composeapps', [])
    if not applications:
        return ()
    projects = {app['project']: app for app in applications}
    try:
        sources = [Path(value).resolve(strict=True) for value in lines(item['paths'])]
    except OSError as exc:
        raise BackupError('Compose backup source is missing') from exc
    for app in applications:
        selected_files(app, sources)
    identifiers = command('ps', '--all', '--quiet', '--no-trunc').split()
    if any(CONTAINER_ID.fullmatch(identifier) is None for identifier in identifiers):
        raise BackupError('Invalid Docker container inventory')
    excludes = lines(item['excludes'])
    services: dict[str, dict[str, ServicePin]] = {project: {} for project in projects}
    replicas: dict[str, dict[str, int]] = {project: {} for project in projects}
    for identifier in identifiers:
        labels = object_value(decode(command('inspect', '--format', '{{json .Config.Labels}}', identifier)))
        project = labels.get('com.docker.compose.project')
        if not isinstance(project, str):
            continue
        if project not in projects:
            continue
        if labels.get('com.docker.compose.oneoff') == 'True':
            continue
        if config_files(labels) != tuple(projects[project]['definitions']):
            raise BackupError(f'Compose project {project} definitions differ from the running application')
        service = string(labels.get('com.docker.compose.service', ''))
        if SERVICE.fullmatch(service) is None:
            raise BackupError(f'Compose project {project} has a container without a service label')
        image = command('inspect', '--format', '{{.Config.Image}}', identifier).strip()
        image_id = command('inspect', '--format', '{{.Image}}', identifier).strip()
        record = ServicePin(
            service, image, pin_image(image, image_id, command), 1, mounts(identifier, sources, excludes, command)
        )
        previous = services[project].setdefault(service, record)
        if previous != record:
            raise BackupError(f'Compose service {project}/{service} has inconsistent replicas')
        replicas[project][service] = replicas[project].get(service, 0) + 1
    if any(not values for values in services.values()):
        raise BackupError('A selected Compose project has no recoverable services')
    return tuple(
        ProjectCapture(
            project,
            tuple(projects[project]['definitions']),
            tuple(projects[project]['envfiles']),
            tuple(projects[project]['secretfiles']),
            tuple(
                ServicePin(
                    service.service,
                    service.image,
                    service.pinned,
                    replicas[project][name],
                    service.binds,
                )
                for name in sorted(services[project])
                for service in (services[project][name],)
            ),
        )
        for project in sorted(projects)
    )


def pull(captures: tuple[ProjectCapture, ...], command: DockerCommand = docker) -> None:
    for pinned in sorted({service.pinned for project in captures for service in project.services}):
        command('pull', pinned)


def manifest(captures: tuple[ProjectCapture, ...]) -> list[ComposeProjectManifest]:
    return [
        ComposeProjectManifest(
            project=project.project,
            definitions=list(project.definitions),
            envfiles=list(project.envfiles),
            secretfiles=list(project.secretfiles),
            services=[
                ComposeServiceManifest(
                    service=service.service,
                    image=service.image,
                    pinned=service.pinned,
                    replicas=service.replicas,
                    binds=[
                        ComposeBindManifest(source=bind.source, target=bind.target, read_only=bind.read_only)
                        for bind in service.binds
                    ],
                )
                for service in project.services
            ],
        )
        for project in captures
    ]


def manifest_projects(value: JSONValue) -> list[ComposeProjectManifest]:
    if not isinstance(value, list) or not 0 < len(value) <= 32:
        raise BackupError('Invalid Compose manifest projects', code='invalid_archive')

    projects: list[ComposeProjectManifest] = []
    for raw in value:
        project = object_value(raw)
        if set(project) != {'project', 'definitions', 'envfiles', 'secretfiles', 'services'}:
            raise BackupError('Invalid Compose manifest project', code='invalid_archive')

        name = string(project['project'])
        if SERVICE.fullmatch(name) is None:
            raise BackupError('Invalid Compose manifest project name', code='invalid_archive')

        files: dict[str, list[str]] = {}
        for field in ('definitions', 'envfiles', 'secretfiles'):
            raw_files = project[field]
            if not isinstance(raw_files, list) or len(raw_files) > 64 or (field == 'definitions' and not raw_files):
                raise BackupError('Invalid Compose manifest files', code='invalid_archive')
            selected = [string(path) for path in raw_files]
            if any(not Path(path).is_absolute() or '..' in Path(path).parts for path in selected):
                raise BackupError('Invalid Compose manifest file path', code='invalid_archive')
            files[field] = selected
        if len(set(files['definitions'] + files['envfiles'] + files['secretfiles'])) != sum(map(len, files.values())):
            raise BackupError('Duplicate Compose manifest file', code='invalid_archive')

        raw_services = project['services']
        if not isinstance(raw_services, list) or not 0 < len(raw_services) <= 256:
            raise BackupError('Invalid Compose manifest services', code='invalid_archive')
        services: list[ComposeServiceManifest] = []
        for raw_service in raw_services:
            service = object_value(raw_service)
            if set(service) != {'service', 'image', 'pinned', 'replicas', 'binds'}:
                raise BackupError('Invalid Compose manifest service', code='invalid_archive')

            service_name = string(service['service'])
            image = string(service['image'])
            pinned = string(service['pinned'])
            if (
                SERVICE.fullmatch(service_name) is None
                or '@' not in pinned
                or DIGEST.fullmatch(pinned.rsplit('@', 1)[1]) is None
                or repository(pinned) != repository(image)
            ):
                raise BackupError('Invalid Compose manifest image', code='invalid_archive')
            replicas = service['replicas']
            if type(replicas) is not int or not 1 <= replicas <= 256:
                raise BackupError('Invalid Compose manifest replica count', code='invalid_archive')

            raw_binds = service['binds']
            if not isinstance(raw_binds, list) or len(raw_binds) > 256:
                raise BackupError('Invalid Compose manifest binds', code='invalid_archive')
            binds: list[ComposeBindManifest] = []
            for raw_bind in raw_binds:
                bind = object_value(raw_bind)
                if set(bind) != {'source', 'target', 'read_only'}:
                    raise BackupError('Invalid Compose manifest bind', code='invalid_archive')

                source = string(bind['source'])
                target = string(bind['target'])
                if (
                    not source.startswith('/')
                    or not target.startswith('/')
                    or '..' in Path(source).parts
                    or '..' in Path(target).parts
                ):
                    raise BackupError('Invalid Compose manifest bind path', code='invalid_archive')
                binds.append(ComposeBindManifest(source=source, target=target, read_only=boolean(bind['read_only'])))

            services.append(
                ComposeServiceManifest(service=service_name, image=image, pinned=pinned, replicas=replicas, binds=binds)
            )
        if len({service['service'] for service in services}) != len(services):
            raise BackupError('Duplicate Compose manifest service', code='invalid_archive')

        projects.append(
            ComposeProjectManifest(
                project=name,
                definitions=files['definitions'],
                envfiles=files['envfiles'],
                secretfiles=files['secretfiles'],
                services=services,
            )
        )
    if len({project['project'] for project in projects}) != len(projects):
        raise BackupError('Duplicate Compose manifest project', code='invalid_archive')

    return projects
