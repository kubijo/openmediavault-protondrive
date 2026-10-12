import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import configuration
from protondrive.common import BackupError
from protondrive.models import Manifest, RemoteEntry
from protondrive.retention import upload_pair


class Remote:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.uploaded: list[str] = []
        self.fail_upload = False

    def list(self, path: str) -> list[RemoteEntry]:
        return [{'name': name, 'type': 'file', 'size': len(data), 'uid': name} for name, data in self.files.items()]

    def upload(self, path: str | Path, folder: str) -> None:
        path = Path(path)
        if self.fail_upload:
            raise BackupError('Injected interruption')
        self.files[path.name] = path.read_bytes()
        self.uploaded.append(path.name)

    def download(self, remote: str, directory: str | Path) -> None:
        name = Path(remote).name
        (Path(directory) / name).write_bytes(self.files[name])

    def trash(self, folder: str, entry: RemoteEntry) -> None:
        del self.files[entry['name']]


class UploadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config, self.item = configuration()
        self.config['minimumfreebytes'] = 0
        self.archive = Path(temporary.name) / 'appData-20261001T0300Z.tar.zst'
        self.archive.write_bytes(b'archive fixture')
        self.value: Manifest = {
            'format': 1,
            'instanceuuid': self.config['instanceuuid'],
            'setuuid': self.item['uuid'],
            'timestamp': '20261001T0300Z',
            'archive': self.archive.name,
            'size': self.archive.stat().st_size,
            'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest(),
        }
        self.manifest = self.archive.with_name(self.archive.name + '.manifest.json')
        self.manifest.write_text(json.dumps(self.value))
        self.remote = Remote()

    def upload(self):
        return upload_pair(self.remote, self.config, self.item, self.archive, '/my-files/test')

    def test_complete_pair_is_idempotent(self):
        self.assertEqual(self.upload(), self.value)
        self.upload()
        self.assertEqual(self.remote.uploaded, [self.archive.name, self.manifest.name])

    def test_interrupted_archive_does_not_publish_manifest(self):
        self.remote.fail_upload = True
        with self.assertRaises(BackupError):
            self.upload()
        self.assertNotIn(self.manifest.name, self.remote.files)
        self.assertTrue(self.archive.exists())

    def test_retry_verifies_uncommitted_archive_before_publishing_manifest(self):
        self.remote.files[self.archive.name] = self.archive.read_bytes()
        self.upload()
        self.assertEqual(self.remote.uploaded, [self.manifest.name])

    def test_same_size_different_content_never_gets_completion_marker(self):
        size = self.value['size']
        assert isinstance(size, int)
        self.remote.files[self.archive.name] = b'x' * size
        with self.assertRaises(BackupError):
            self.upload()
        self.assertEqual(self.remote.uploaded, [])
        self.assertNotIn(self.manifest.name, self.remote.files)

    def test_upload_skip_race_cannot_publish_a_foreign_archive(self):
        uploaded = self.remote.upload

        def skip_after_foreign_file(path: Path, folder: str) -> None:
            if path.name == self.archive.name:
                self.remote.files[path.name] = b'x' * self.archive.stat().st_size
            else:
                uploaded(path, folder)

        with (
            patch.object(self.remote, 'upload', side_effect=skip_after_foreign_file),
            self.assertRaisesRegex(BackupError, 'checksum'),
        ):
            self.upload()
        self.assertNotIn(self.manifest.name, self.remote.files)

    def test_corrupt_completed_pair_is_not_accepted_as_idempotent(self):
        self.upload()
        self.remote.files[self.archive.name] = b'x' * self.archive.stat().st_size
        with self.assertRaisesRegex(BackupError, 'checksum'):
            self.upload()
        self.assertEqual(self.remote.uploaded, [self.archive.name, self.manifest.name])

    def test_changed_local_archive_never_uploads(self):
        self.archive.write_bytes(b'changed archive')
        with self.assertRaises(BackupError):
            self.upload()
        self.assertEqual(self.remote.uploaded, [])
