"""The service survives clients that disconnect before reading their reply."""

import multiprocessing
import signal
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import configuration
from protondrive import daemon
from protondrive.common import BackupError
from protondrive.json_data import JSONValue, decode, object_value
from protondrive.models import Configuration


class ServiceFixture:
    def __init__(self, config: Configuration) -> None:
        pass

    def schedule_probe(self):
        pass

    def dispatch(self, message: dict[str, JSONValue]) -> bool:
        if message['operation'] == 'disconnect':
            time.sleep(0.1)
        return True


def run_service(path: Path) -> None:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    with (
        patch.object(daemon, 'SOCKET', path),
        patch.object(daemon, 'Service', ServiceFixture),
        patch.object(daemon, 'load', return_value={}),
    ):
        daemon.serve()


class DaemonTests(unittest.TestCase):
    def test_foreign_browsing_never_claims_or_creates_storage(self) -> None:
        config, item = configuration()
        service = object.__new__(daemon.Service)
        service.config = config
        listing = Mock(return_value=[])
        ownership = Mock()
        folders = Mock()
        service.cli = Mock(list=listing, ensure_instance_owned=ownership, ensure_folder=folders)
        service.operations = threading.Lock()
        foreign = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
        self.assertEqual(
            service.dispatch({'operation': 'browse', 'instanceuuid': foreign, 'setuuid': item['uuid']}), []
        )
        listing.assert_called_once_with(f'{config["remotepath"]}/{foreign}/{item["uuid"]}')
        ownership.assert_not_called()
        folders.assert_not_called()
        listing.reset_mock()
        for message in (
            {'operation': 'browse', 'instanceuuid': '../outside'},
            {'operation': 'browse', 'setuuid': item['uuid']},
        ):
            with self.assertRaises(BackupError):
                service.dispatch(object_value(message))
        listing.assert_not_called()

    def test_invalid_requests_do_not_create_remote_storage(self):
        config, item = configuration()
        service = object.__new__(daemon.Service)
        service.config = config
        ensure = Mock()
        service.cli = Mock(ensure_instance_owned=ensure)
        service.operations = threading.Lock()
        for message in (
            {'operation': 'unknown', 'setuuid': item['uuid']},
            {'operation': 'upload', 'setuuid': item['uuid'], 'name': '../outside.tar.zst'},
        ):
            with self.subTest(message=message), self.assertRaises(BackupError):
                service.dispatch(object_value(message))
        ensure.assert_not_called()

    def test_disconnected_client_does_not_kill_the_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'control.sock'
            process = multiprocessing.get_context('fork').Process(target=run_service, args=(path,))
            process.start()
            try:
                deadline = time.monotonic() + 5
                while not path.exists():
                    self.assertTrue(process.is_alive(), f'Service exited: {process.exitcode}')
                    self.assertLess(time.monotonic(), deadline, 'Service socket was not created')
                    time.sleep(0.01)
                with socket.socket(socket.AF_UNIX) as client:
                    client.connect(str(path))
                    client.sendall(b'{"operation":"disconnect"}\n')
                time.sleep(0.2)
                self.assertTrue(process.is_alive(), f'Broken pipe killed service: {process.exitcode}')
                with socket.socket(socket.AF_UNIX) as client:
                    client.connect(str(path))
                    client.sendall(b'{"operation":"status"}\n')
                    with client.makefile('rb') as stream:
                        reply = decode(stream.readline())
                self.assertEqual(reply, {'ok': True, 'result': True})
            finally:
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join()
