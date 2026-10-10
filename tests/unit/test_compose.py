"""Compose capture must refuse incomplete or unpullable application state."""

import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from protondrive.common import BackupError
from protondrive.compose import capture, manifest, pull
from protondrive.models import BackupSet
from protondrive.recovery import DockerCommand
from protondrive.retention import archive_metadata

CONTAINER = 'a' * 64
IMAGE = 'b' * 64
DIGEST = 'c' * 64


def backup_set(source: Path) -> BackupSet:
    definition = source / 'compose.yaml'
    definition.touch(exist_ok=True)
    return BackupSet(
        uuid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        name='appData',
        enable=True,
        paths=str(source),
        excludes='',
        stopcontainers=False,
        composeprojects='selected',
        composeapps=[{'project': 'selected', 'definitions': [str(definition)], 'envfiles': [], 'secretfiles': []}],
        localkeep=2,
        remotekeep=7,
    )


def docker_fixture(mount: Mapping[str, object], definition: Path, *, digests: list[str] | None = None) -> DockerCommand:
    def command(*args: str) -> str:
        if args == ('ps', '--all', '--quiet', '--no-trunc'):
            return CONTAINER
        if args[:2] == ('inspect', '--format') and args[-1] == CONTAINER:
            match args[2]:
                case '{{json .Config.Labels}}':
                    return json.dumps(
                        {
                            'com.docker.compose.project': 'selected',
                            'com.docker.compose.service': 'web',
                            'com.docker.compose.project.working_dir': str(definition.parent),
                            'com.docker.compose.project.config_files': str(definition),
                        }
                    )
                case '{{.Config.Image}}':
                    return 'nginx:stable'
                case '{{.Image}}':
                    return 'sha256:' + IMAGE
                case '{{json .Mounts}}':
                    return json.dumps([mount])
                case _:
                    raise AssertionError(f'Unexpected Docker inspection: {args}')
        if args == ('image', 'inspect', '--format', '{{json .RepoDigests}}', 'sha256:' + IMAGE):
            return json.dumps(digests if digests is not None else ['docker.io/library/nginx@sha256:' + DIGEST])
        raise AssertionError(f'Unexpected Docker operation: {args}')

    return command


class ComposeCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_capture_pins_image_and_explicit_bind_without_reading_environment(self) -> None:
        source = self.root / 'app'
        source.mkdir()
        mount = {'Type': 'bind', 'Source': str(source), 'Destination': '/data', 'RW': False}

        [project] = capture(backup_set(source), docker_fixture(mount, source / 'compose.yaml'))

        self.assertEqual(project.project, 'selected')
        [service] = project.services
        self.assertEqual(service.service, 'web')
        self.assertEqual(service.pinned, 'docker.io/library/nginx@sha256:' + DIGEST)
        self.assertEqual(service.binds[0].source, str(source))
        self.assertTrue(service.binds[0].read_only)
        self.assertEqual(service.replicas, 1)

        pair = manifest((project,))
        record = {
            'format': 2,
            'instanceuuid': 'instance',
            'setuuid': 'set',
            'archive': 'appData-20261010T0300Z.tar.zst',
            'timestamp': '20261010T0300Z',
            'size': 123,
            'sha256': 'd' * 64,
            'compose': pair,
        }
        self.assertEqual(
            archive_metadata(record, 'instance', 'set', 'appData-20261010T0300Z.tar.zst').get('compose'), pair
        )

        pulled: list[tuple[str, ...]] = []
        pull((project,), lambda *args: pulled.append(args) or '')
        self.assertEqual(pulled, [('pull', service.pinned)])

        pair[0]['services'][0]['pinned'] = 'docker.io/library/nginx@sha256:invalid'
        with self.assertRaisesRegex(BackupError, 'manifest'):
            archive_metadata(record, 'instance', 'set', 'appData-20261010T0300Z.tar.zst')

    def test_capture_rejects_unbacked_mounts(self) -> None:
        cases: list[tuple[dict[str, object], str]] = [
            (
                {'Type': 'volume', 'Source': '/var/lib/docker/volumes/data', 'Destination': '/data', 'RW': True},
                'volume',
            ),
            ({'Type': 'bind', 'Source': '/etc', 'Destination': '/data', 'RW': True}, 'not selected'),
        ]
        for mount, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(BackupError, message):
                capture(backup_set(self.root), docker_fixture(mount, self.root / 'compose.yaml'))

    def test_capture_rejects_exclusions_inside_bind_source(self) -> None:
        source = self.root / 'app'
        source.mkdir()
        cache = source / 'cache'
        cache.mkdir()
        item = backup_set(source)
        item['excludes'] = 'cache'
        for bind_source in (source, cache):
            mount = {'Type': 'bind', 'Source': str(bind_source), 'Destination': '/data', 'RW': True}
            with self.subTest(bind_source=bind_source), self.assertRaisesRegex(BackupError, 'archive exclusion'):
                capture(item, docker_fixture(mount, source / 'compose.yaml'))

    def test_capture_rejects_local_image_without_registry_digest(self) -> None:
        with self.assertRaisesRegex(BackupError, 'pullable registry digest'):
            capture(backup_set(self.root), docker_fixture({'Type': 'bind'}, self.root / 'compose.yaml', digests=[]))
