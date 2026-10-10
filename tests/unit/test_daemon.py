"""The service survives clients that disconnect before reading their reply."""

import multiprocessing
import signal
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import configuration
from protondrive import daemon
from protondrive.common import BackupError, request
from protondrive.json_data import JSONValue, decode, object_value
from protondrive.models import AuthStatus, Configuration, Destination, auth_status
from protondrive.records import backend_statuses


def unavailable(error: str) -> AuthStatus:
    return auth_status('unavailable', error=error)


class ServiceFixture:
    def __init__(self, config: Configuration) -> None:
        pass

    def schedule_probe(self):
        pass

    def dispatch(self, message: dict[str, JSONValue]) -> bool:
        if message['operation'] == 'disconnect':
            time.sleep(0.1)
        if message['operation'] == 'busy':
            raise BackupError('Proton service is busy', code='busy')
        if message['operation'] == 'unexpected':
            raise RuntimeError('private vendor diagnostic')
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
    def test_authentication_and_cancellation_route_to_independent_backends(self) -> None:
        service = object.__new__(daemon.Service)
        service.config, _ = configuration()
        service.config['destinations'].append(
            Destination(id='secondary', kind='future', name='Secondary', enable=True, root='/backups')
        )
        first_auth = Mock(return_value=auth_status('signing-in'))
        second_auth = Mock(return_value=auth_status('signing-in'))
        first_cancel = Mock()
        second_cancel = Mock()
        first = Mock(start_auth=first_auth, cancel_transfer=first_cancel)
        second = Mock(start_auth=second_auth, cancel_transfer=second_cancel)
        service.backends = {'protondrive': first, 'secondary': second}
        self.assertEqual(service.dispatch({'operation': 'start-auth'}), auth_status('signing-in'))
        self.assertEqual(
            service.dispatch({'operation': 'start-auth', 'destinationid': 'secondary'}), auth_status('signing-in')
        )
        first_auth.assert_called_once_with()
        second_auth.assert_called_once_with()
        service.dispatch({'operation': 'cancel-transfer'})
        first_cancel.assert_called_once_with()
        second_cancel.assert_called_once_with()

    def test_status_reports_each_backend_separately(self) -> None:
        service = object.__new__(daemon.Service)
        service.config, _ = configuration()
        service.config['destinations'].append(
            Destination(id='secondary', kind='future', name='Secondary', enable=True, root='/backups')
        )
        transfer = {'transferphase': '', 'transferfile': '', 'transferelapsed': 0}
        first = Mock(
            status=Mock(return_value=auth_status('signed-in', email='first@example.org')),
            transfer_status=Mock(return_value=transfer),
        )
        second = Mock(
            status=Mock(return_value=auth_status('signed-out')),
            transfer_status=Mock(return_value=transfer),
        )
        service.backends = {'protondrive': first, 'secondary': second}
        with patch.object(daemon.Service, 'schedule_probe'):
            result = backend_statuses(service.dispatch({'operation': 'backend-statuses'}))
        self.assertEqual([entry['state'] for entry in result], ['signed-in', 'signed-out'])
        self.assertEqual([entry['id'] for entry in result], ['protondrive', 'secondary'])

    def test_probe_redacts_unexpected_account_diagnostic(self) -> None:
        service = object.__new__(daemon.Service)
        service.backends = {
            'protondrive': Mock(
                probe=Mock(side_effect=RuntimeError('private vendor diagnostic')),
                probe_failed=unavailable,
            )
        }
        with self.assertLogs('protondrive.daemon', level='ERROR'):
            result = service.probe()
        self.assertEqual(result['state'], 'unavailable')
        self.assertRegex(result['error'], r'reference [0-9a-f]{12}')
        self.assertNotIn('private vendor diagnostic', result['error'])

    def test_failed_probe_disables_stale_signed_in_state(self) -> None:
        service = object.__new__(daemon.Service)
        service.backends = {
            'protondrive': Mock(
                probe=Mock(side_effect=BackupError('Proton operation timed out', code='unavailable')),
                probe_failed=unavailable,
            )
        }
        result = service.probe()
        self.assertEqual(result['state'], 'unavailable')
        self.assertEqual(result['error'], 'Proton operation timed out')

    def test_browsing_waits_for_an_inflight_operation(self) -> None:
        service = object.__new__(daemon.Service)
        service.config, _ = configuration()
        listing = Mock(return_value=[])
        service.backends = {'protondrive': Mock(list=listing)}
        service.operations = threading.Lock()
        with ThreadPoolExecutor(max_workers=1) as workers:
            service.operations.acquire()
            try:
                pending = workers.submit(service.dispatch, {'operation': 'browse'})
                with self.assertRaises(TimeoutError):
                    pending.result(timeout=0.05)
                listing.assert_not_called()
            finally:
                service.operations.release()
            self.assertEqual(pending.result(timeout=2), [])
        listing.assert_called_once_with(service.config['remotepath'])

    def test_mutations_still_refuse_busy_service_and_cancellation_remains_available(self) -> None:
        service = object.__new__(daemon.Service)
        service.config, _ = configuration()
        service.operations = threading.Lock()
        cancel = Mock()
        service.backends = {'protondrive': Mock(cancel_transfer=cancel)}
        with ThreadPoolExecutor(max_workers=1) as workers, service.operations:
            pending = workers.submit(service.dispatch, {'operation': 'upload'})
            with self.assertRaisesRegex(BackupError, 'busy'):
                pending.result(timeout=1)
            self.assertTrue(service.dispatch({'operation': 'cancel-transfer'}))
        cancel.assert_called_once_with()

    def test_foreign_browsing_never_claims_or_creates_storage(self) -> None:
        config, item = configuration()
        service = object.__new__(daemon.Service)
        service.config = config
        listing = Mock(return_value=[])
        ownership = Mock()
        folders = Mock()
        service.backends = {'protondrive': Mock(list=listing, ensure_instance_owned=ownership, ensure_folder=folders)}
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
        service.backends = {'protondrive': Mock(ensure_instance_owned=ensure)}
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

    def test_ipc_preserves_expected_code_and_redacts_unexpected_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'control.sock'
            process = multiprocessing.get_context('fork').Process(target=run_service, args=(path,))
            process.start()
            try:
                deadline = time.monotonic() + 5
                while not path.exists():
                    self.assertTrue(process.is_alive())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                with patch('protondrive.common.SOCKET', path):
                    with self.assertRaises(BackupError) as busy:
                        request('busy')
                    self.assertEqual(busy.exception.code, 'busy')
                    with self.assertRaises(BackupError) as unexpected:
                        request('unexpected')
                    self.assertEqual(unexpected.exception.code, 'internal')
                    self.assertRegex(str(unexpected.exception), r'reference [0-9a-f]{12}')
                    self.assertNotIn('private vendor diagnostic', str(unexpected.exception))
            finally:
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join()
