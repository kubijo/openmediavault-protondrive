"""Read-only foreign downloads and cancellation scoped to the owning job."""

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from helpers import configuration
from protondrive.json_data import JSONValue
from protondrive.models import RemoteEntry
from protondrive.operation import Cancelled
from protondrive.restore_download import Downloads, download


class Remote:
    def __init__(self, instance: str, set_id: str) -> None:
        self.name = 'system-20261006T1200Z.tar.zst'
        self.data = b'compressed archive fixture'
        self.manifest = json.dumps(
            {
                'format': 1,
                'instanceuuid': instance,
                'setuuid': set_id,
                'archive': self.name,
                'timestamp': '20261006T1200Z',
                'size': len(self.data),
                'sha256': hashlib.sha256(self.data).hexdigest(),
            }
        ).encode()
        self.calls: list[str] = []
        self.started = threading.Event()
        self.finish = threading.Event()
        self.finish.set()

    def list(self, path: str) -> list[RemoteEntry]:
        self.calls.append(path)
        return [
            {'name': self.name, 'type': 'file', 'uid': 'archive', 'size': len(self.data)},
            {'name': self.name + '.manifest.json', 'type': 'file', 'uid': 'manifest', 'size': len(self.manifest)},
        ]

    def download(self, remote: str, directory: str | Path) -> None:
        self.calls.append(remote)
        manifest = remote.endswith('.manifest.json')
        if not manifest:
            self.started.set()
            if not self.finish.wait(5):
                raise RuntimeError('Test download did not finish')
        (Path(directory) / Path(remote).name).write_bytes(self.manifest if manifest else self.data)

    def upload(self, path: str | Path, folder: str) -> None:
        raise AssertionError('Read-only restore must not upload')

    def trash(self, folder: str, entry: RemoteEntry) -> None:
        raise AssertionError('Read-only restore must not delete')


class DownloadTests(unittest.TestCase):
    def test_foreign_archive_stays_under_configured_root_without_mutations(self) -> None:
        config, item = configuration()
        foreign = str(uuid4())
        remote = Remote(foreign, item['uuid'])
        job = str(uuid4())
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            result = download(
                remote,
                config,
                {
                    'instanceuuid': foreign,
                    'setuuid': item['uuid'],
                    'jobuuid': job,
                    'name': remote.name,
                },
                cache,
            )
            self.assertEqual(result['instanceuuid'], foreign)
            self.assertEqual((cache / job / remote.name).read_bytes(), remote.data)
        self.assertTrue(
            all(path.startswith(f'{config["remotepath"]}/{foreign}/{item["uuid"]}') for path in remote.calls)
        )

    def test_cancellation_before_admission_has_no_storage_activity(self) -> None:
        config, item = configuration()
        remote = Remote(config['instanceuuid'], item['uuid'])
        registry = Downloads(lambda _: None)
        job = str(uuid4())
        registry.cancel(job)
        with self.assertRaises(Cancelled):
            registry.run(remote, config, {'jobuuid': job})
        self.assertEqual(remote.calls, [])

    def test_cancellation_cannot_stop_a_different_job(self) -> None:
        config, item = configuration()
        remote = Remote(config['instanceuuid'], item['uuid'])
        remote.finish.clear()
        registry = Downloads(lambda _: remote.finish.set())
        job = str(uuid4())
        value: dict[str, JSONValue] = {
            'instanceuuid': config['instanceuuid'],
            'setuuid': item['uuid'],
            'jobuuid': job,
            'name': remote.name,
        }
        failures: list[Exception] = []
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def run() -> None:
                try:
                    registry.run(remote, config, value, cache)
                except (Cancelled, OSError, RuntimeError) as error:
                    failures.append(error)

            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(remote.started.wait(2))
                registry.cancel(str(uuid4()))
                self.assertFalse(remote.finish.wait(0.2))
                registry.cancel(job)
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(failures), 1)
                self.assertIsInstance(failures[0], Cancelled)
                self.assertFalse((cache / job).exists())
            finally:
                remote.finish.set()
                worker.join(5)
