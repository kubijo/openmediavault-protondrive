"""Fault injection must release only its own network rules on every exit path."""

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.integration import live_ui_guest as flow
from tests.integration import live_upload_retry as retry
from tests.integration.flow_records import retry_record

from helpers import configuration
from protondrive.archive import digest

TOKEN = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


class UploadRetryTests(unittest.TestCase):
    def test_cleanup_retains_recovery_state_if_service_restart_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'flow'
            root.mkdir()
            (root / 'retry.json').write_text('{}')
            (root / 'record.json').write_text('{"containers": []}')
            helper = SimpleNamespace(
                disarm=Mock(),
                remove_fixture=Mock(),
                wait_ready=Mock(side_effect=RuntimeError('service unavailable')),
            )
            with (
                patch.object(flow, 'FLOW', root),
                patch.object(flow, 'STATE', root / 'state'),
                patch.object(flow, 'OVERRIDE', root / 'override'),
                patch.object(flow, 'active', return_value=False),
                patch.object(flow, 'run', return_value=SimpleNamespace(stdout=b'')),
                patch.object(flow, 'retry_module', return_value=helper),
                self.assertRaisesRegex(RuntimeError, 'service unavailable'),
            ):
                flow.cleanup()
            self.assertTrue((root / 'retry.json').exists())

    def test_fixture_configuration_uses_the_deployed_service_permissions(self) -> None:
        with (
            patch.object(flow.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=123)),
            patch.object(flow, 'atomic_json') as write,
        ):
            flow.write_config({'fixture': True})
        write.assert_called_once_with(flow.CONFIG, {'fixture': True}, 0o640, owner=(0, 123))

    def test_network_cleanup_checks_ownership_and_refuses_foreign_rules(self) -> None:
        value = retry_record({'token': TOKEN})
        owned = {'name': retry.table_name(value), 'family': 'inet'}
        foreign = {'name': retry.TABLE + '_foreign', 'family': 'inet'}
        for current in (None, owned, foreign):
            with (
                self.subTest(current=current),
                patch.object(flow, 'guard_vm'),
                patch.object(retry, 'nft_items', return_value=[] if current is None else [{'table': current}]),
                patch.object(retry, 'read_record', return_value=value),
                patch.object(retry, 'nft') as nft,
            ):
                if current == foreign:
                    with self.assertRaisesRegex(RuntimeError, 'not owned'):
                        retry.restore_network()
                    nft.assert_not_called()
                else:
                    retry.restore_network()
                    if current is None:
                        nft.assert_not_called()
                    else:
                        nft.assert_called_once_with('delete', 'table', 'inet', retry.table_name(value))

    def test_foreign_table_does_not_prevent_owned_fault_cleanup(self) -> None:
        value = retry_record({'token': TOKEN})
        with (
            patch.object(flow, 'guard_vm'),
            patch.object(retry, 'read_record', return_value=value),
            patch.object(
                retry,
                'nft_items',
                return_value=[
                    {'table': {'name': retry.table_name(value), 'family': 'inet'}},
                    {'table': {'name': retry.TABLE + '_foreign', 'family': 'inet'}},
                ],
            ),
            patch.object(retry, 'nft') as nft,
            self.assertRaisesRegex(RuntimeError, 'not owned'),
        ):
            retry.restore_network()
        nft.assert_called_once_with('delete', 'table', 'inet', retry.table_name(value))

    def test_failed_watcher_stop_still_restores_network_and_reports_failure(self) -> None:
        failure = subprocess.CalledProcessError(1, ['systemctl', 'stop'])
        with (
            patch.object(
                flow,
                'run',
                side_effect=[
                    SimpleNamespace(stdout=b'active'),
                    SimpleNamespace(stdout=f'Proton upload fault {TOKEN}'.encode()),
                    failure,
                ],
            ),
            patch.object(retry, 'read_record', return_value=retry_record({'token': TOKEN})),
            patch.object(retry, 'restore_network') as restore,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            retry.disarm()
        restore.assert_called_once()

    def test_disarm_refuses_foreign_watcher_but_still_releases_owned_network_rules(self) -> None:
        with (
            patch.object(
                flow, 'run', side_effect=[SimpleNamespace(stdout=b'active'), SimpleNamespace(stdout=b'foreign')]
            ) as run,
            patch.object(retry, 'read_record', return_value=retry_record({'token': TOKEN})),
            patch.object(retry, 'restore_network') as restore,
            self.assertRaisesRegex(RuntimeError, 'not owned'),
        ):
            retry.disarm()
        self.assertEqual(run.call_count, 2)
        restore.assert_called_once()

    def test_cleanup_entrypoints_do_not_depend_on_mutable_configuration(self) -> None:
        for module, action, method, main in (
            (retry, 'restore-network', 'restore_network', retry.main),
            (flow, 'cleanup', 'cleanup', flow.main),
        ):
            with (
                self.subTest(action=action),
                patch('sys.argv', ['fixture', action]),
                patch.object(retry.signal, 'signal'),
                patch.object(flow, 'guard_vm') as guard,
                patch.object(module, 'load', side_effect=AssertionError('must not read changed configuration')),
                patch.object(module, method, return_value={}) as cleanup,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main()
                cleanup.assert_called_once()
                guard.assert_called_once()

    def test_payload_cleanup_refuses_broken_symlinks_and_changed_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'payload'
            value = retry_record({'token': TOKEN})
            with patch.object(retry, 'PAYLOAD', path), patch.object(retry, 'read_record', return_value=value):
                path.symlink_to(path.parent / 'missing')
                with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                    retry.remove_fixture()
                self.assertTrue(path.is_symlink())
                path.unlink()
                path.write_bytes(b'original')
                value['payload_identity'] = [path.stat().st_dev, path.stat().st_ino]
                value['payload_sha256'] = digest(path)
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'contents changed'):
                    retry.remove_fixture()
                self.assertEqual(path.read_bytes(), b'changed')
                path.write_bytes(b'original')
                retry.remove_fixture()
                self.assertFalse(path.exists())

    def test_service_startup_retry_is_bounded(self) -> None:
        with (
            patch.object(
                retry, 'request', side_effect=[FileNotFoundError(), ConnectionRefusedError(), TimeoutError(), {}]
            ) as request,
            patch.object(retry.time, 'sleep'),
        ):
            retry.wait_ready()
            self.assertEqual(request.call_count, 4)
            request.assert_called_with('status', timeout=1)
        with (
            patch.object(retry, 'request', side_effect=ConnectionRefusedError()),
            self.assertRaisesRegex(RuntimeError, 'did not restart'),
        ):
            retry.wait_ready(timeout=0)
        with (
            patch.object(retry, 'request', side_effect=TimeoutError()) as request,
            self.assertRaisesRegex(RuntimeError, 'did not restart'),
        ):
            retry.wait_ready(timeout=0)
        request.assert_called_once_with('status', timeout=0.01)

    def test_failure_assertion_waits_for_watcher_and_rejects_missing_evidence(self) -> None:
        initial = retry_record({'token': TOKEN, 'injected': True})
        final = {**initial, 'failure_observed': True}
        with (
            patch.object(retry, 'read_record', side_effect=[initial, final]),
            patch.object(retry.time, 'sleep') as sleep,
        ):
            self.assertEqual(retry.wait_failure()['token'], TOKEN)
            sleep.assert_called_once()
        with (
            patch.object(retry, 'read_record', return_value=initial),
            self.assertRaisesRegex(RuntimeError, 'not proven'),
        ):
            retry.wait_failure(timeout=0)

    def test_watcher_cancellation_and_timeout_restore_network(self) -> None:
        config, _ = configuration()
        for cancelled in (True, False):
            value = retry_record({'token': TOKEN})
            with (
                self.subTest(cancelled=cancelled),
                patch.object(retry, 'load', return_value=config),
                patch.object(flow, 'guard'),
                patch.object(retry, 'read_record', return_value=value),
                patch.object(retry, 'save_record'),
                patch.object(retry.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=123)),
                patch.object(retry, 'table', return_value=None),
                patch.object(retry, 'nft'),
                patch.object(retry, 'request', side_effect=KeyboardInterrupt('cancelled')),
                patch.object(retry.time, 'monotonic', side_effect=[0, 1 if cancelled else 211]),
                patch.object(retry, 'restore_network') as restore,
                self.assertRaises(KeyboardInterrupt if cancelled else RuntimeError),
            ):
                retry.watch()
            restore.assert_called_once()
            self.assertTrue(value['watcher_error'])
