"""Notification observations must not change backup or authentication state."""

import contextlib
import io
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from protondrive import health
from protondrive.common import BackupError, atomic_json, locked
from protondrive.completion import record_completion
from protondrive.json_data import decode


class HealthTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(health, 'STATE', self.state))
        self.unit = {
            'LoadState': 'loaded',
            'ActiveState': 'inactive',
            'Result': 'success',
            'ExecMainStartTimestampMonotonic': '0',
        }
        self.stack.enter_context(patch.object(health, 'unit_status', return_value=self.unit))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def status(self, phase: str, success: str | None = None) -> None:
        if success is None:
            success = '2026-10-06T09:00:00+00:00' if phase == 'completed' else ''
        atomic_json(self.state / 'status.json', {'phase': phase, 'lastsuccess': success})

    def check_poll_during_completion(self, failed: bool) -> None:
        first = '2026-10-06T09:00:00+00:00'
        second = '2026-10-06T10:00:00+00:00'
        record_completion(self.state, first)
        self.status('completed', first)
        self.assertEqual(health.check_health('backup'), 0)
        if failed:
            self.unit['Result'] = 'exit-code'
            self.assertEqual(health.check_health('backup'), 1)
        self.unit.update(ActiveState='activating', Result='success')
        writing = threading.Event()
        release = threading.Event()

        def held_write(path: Path, value: object) -> None:
            writing.set()
            if not release.wait(timeout=3):
                raise TimeoutError('Completion test did not release the write')
            atomic_json(path, value)

        with ThreadPoolExecutor() as executor, patch('protondrive.completion.atomic_json', side_effect=held_write):
            writer = executor.submit(record_completion, self.state, second)
            try:
                self.assertTrue(writing.wait(timeout=2))
                poll = executor.submit(health.check_health, 'backup')
                # It must wait, rather than turn normal contention into an error
                # or announce recovery before reading the committed state.
                with self.assertRaises(TimeoutError):
                    poll.result(timeout=0.1)
            finally:
                release.set()
            writer.result(timeout=2)
            self.assertEqual(poll.result(timeout=2), int(failed))
        self.unit['ActiveState'] = 'inactive'
        self.status('completed', second)
        self.assertEqual(health.check_health('backup'), 0)

    def test_poll_waits_for_completion_without_manufacturing_an_incident(self) -> None:
        self.check_poll_during_completion(failed=False)

    def test_poll_waits_for_completion_without_clearing_an_existing_incident(self) -> None:
        self.check_poll_during_completion(failed=True)

    def test_lock_timeout_is_incomplete_and_preserves_the_last_observation(self) -> None:
        self.status('failed')
        self.assertEqual(health.check_health('backup'), 1)
        path = self.state / 'health-backup.json'
        before = path.read_bytes()
        with locked(self.state / 'health-backup.lock'), patch.object(health, 'observe') as observe:
            started = time.monotonic()
            self.assertEqual(health.check_health('backup'), 2)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 5)
            self.assertLess(elapsed, 7)
            observe.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.unit['ActiveState'] = 'activating'
        self.assertEqual(health.check_health('backup'), 1)

    def test_reboot_cannot_clear_a_start_failure_using_an_older_success(self) -> None:
        self.status('completed', '2026-10-05T09:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 0)
        # ExecStartPre fails before the runner writes a new status.json.
        self.unit['Result'] = 'exit-code'
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['Result'] = 'success'  # systemd loses this result on reboot/reset-failed.
        self.assertEqual(health.check_health('backup'), 1)
        self.assertEqual(health.check_health('backup'), 1)
        self.status('completed', '2026-10-06T09:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 0)

    def test_a_backup_starting_during_observation_does_not_create_an_incident(self) -> None:
        before = dict(self.unit)
        after = {**before, 'ActiveState': 'activating', 'ExecMainStartTimestampMonotonic': '42'}
        self.status('preflight')
        with patch.object(health, 'unit_status', side_effect=[before, after]):
            self.assertEqual(health.check_health('backup'), 0)

    def test_restored_legacy_status_cannot_move_the_failure_baseline_backwards(self) -> None:
        self.status('completed', '2026-10-05T09:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 0)
        self.unit['Result'] = 'exit-code'
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['Result'] = 'success'
        for phase, timestamp in (
            ('completed', '2026-10-04T09:00:00+00:00'),
            ('failed', '2026-10-03T09:00:00+00:00'),
            ('completed', '2026-10-05T12:00:00+03:00'),
            ('completed', '2026-10-05T09:00:00Z'),
        ):
            self.status(phase, timestamp)
            self.assertEqual(health.check_health('backup'), 1)
        self.status('completed', '2026-10-06T09:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 0)

    def test_recorded_success_survives_status_restore_and_backwards_clock_changes(self) -> None:
        first = '2026-10-05T09:00:00+00:00'
        record_completion(self.state, first)
        self.status('completed', first)
        self.assertEqual(health.check_health('backup'), 0)
        self.unit['Result'] = 'exit-code'
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['Result'] = 'success'
        self.status('completed', '2026-10-04T09:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 1)
        self.status('completed', first)
        self.assertEqual(health.check_health('backup'), 1)
        # Only the successful runner advances this record. Wall-clock ordering
        # is irrelevant once both observations have recorded generations.
        second = '2026-10-03T09:00:00+00:00'
        record_completion(self.state, second)
        self.status('completed', second)
        self.assertEqual(health.check_health('backup'), 0)
        self.assertEqual(
            decode((self.state / 'health-backup.json').read_text()),
            {'failed': False, 'success': first, 'generation': 2},
        )
        self.unit['Result'] = 'exit-code'
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['Result'] = 'success'
        # Removing the completion record cannot enable timestamp-only recovery.
        (self.state / 'backup-completion.json').unlink()
        self.status('completed', '2026-10-07T09:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 1)

    def test_first_recorded_retry_clears_a_legacy_incident_after_clock_correction(self) -> None:
        self.status('completed', '2026-10-06T12:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 0)
        self.unit['Result'] = 'exit-code'
        self.assertEqual(health.check_health('backup'), 1)
        timestamp = '2026-10-06T10:00:00+00:00'
        record_completion(self.state, timestamp)
        self.status('completed', timestamp)
        self.unit['Result'] = 'success'
        self.assertEqual(health.check_health('backup'), 0)
        self.assertEqual(health.check_health('backup'), 0)

    def check_counter_recovery(self, restore: bool) -> None:
        path = self.state / 'backup-completion.json'
        first = '2026-10-06T09:00:00+00:00'
        record_completion(self.state, first)
        snapshot = path.read_bytes()
        for hour in (10, 11):
            timestamp = f'2026-10-06T{hour}:00:00+00:00'
            record_completion(self.state, timestamp)
            self.status('completed', timestamp)
            self.assertEqual(health.check_health('backup'), 0)
        self.unit['Result'] = 'exit-code'
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['Result'] = 'success'
        if restore:
            path.write_bytes(snapshot)
            self.status('completed', first)
        else:
            path.unlink()
            self.status('completed', '2026-10-07T09:00:00+00:00')
        # File manipulation alone must never announce recovery.
        self.assertEqual(health.check_health('backup'), 1)
        timestamp = '2026-10-06T08:00:00+00:00'
        record_completion(self.state, timestamp)
        self.status('completed', timestamp)
        self.assertEqual(health.check_health('backup'), 0)

    def test_successful_retry_recovers_after_completion_record_loss(self) -> None:
        self.check_counter_recovery(restore=False)

    def test_successful_retry_recovers_after_restoring_an_older_counter(self) -> None:
        self.check_counter_recovery(restore=True)

    def test_an_inconsistent_snapshot_cannot_clear_an_existing_incident(self) -> None:
        self.status('failed')
        self.assertEqual(health.check_health('backup'), 1)
        self.status('completed')
        before = dict(self.unit)
        after = {**before, 'ActiveState': 'activating', 'ExecMainStartTimestampMonotonic': '42'}
        with patch.object(health, 'unit_status', side_effect=[before, after]):
            self.assertEqual(health.check_health('backup'), 1)

    def test_failed_backup_stays_failed_through_retry_and_only_completed_run_clears(self) -> None:
        self.assertEqual(health.check_health('backup'), 0)
        self.status('failed')
        self.assertEqual(health.check_health('backup'), 1)
        # New invocations only share the durable observation, including after reboot.
        self.status('preflight')
        self.unit['ActiveState'] = 'activating'
        self.assertEqual(health.check_health('backup'), 1)
        self.status('uploading')
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['ActiveState'] = 'inactive'
        self.status('completed')
        self.assertEqual(health.check_health('backup'), 0)
        self.assertEqual(health.check_health('backup'), 0)

    def test_systemd_start_failure_and_killed_runner_are_unhealthy(self) -> None:
        self.status('completed')
        self.unit['Result'] = 'exit-code'
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['Result'] = 'success'
        for phase in ('archiving', 'preflight', 'recovery-failed', 'uploading'):
            self.status(phase)
            self.assertEqual(health.check_health('backup'), 1)

    def test_missing_or_invalid_inputs_fail_without_changing_source(self) -> None:
        self.unit['ExecMainStartTimestampMonotonic'] = '123'
        self.assertEqual(health.check_health('backup'), 2)
        for data in (
            'broken JSON',
            '[]',
            '{}',
            '{"phase": false}',
            '{"phase": "completed"}',
            '{"phase": "completed", "lastsuccess": "not a timestamp"}',
            '{"phase": "completed", "lastsuccess": "2026-10-05T09:00:00"}',
        ):
            (self.state / 'status.json').write_text(data)
            self.assertEqual(health.check_health('backup'), 2)
            self.assertEqual((self.state / 'status.json').read_text(), data)
        self.status('completed')
        (self.state / 'health-backup.json').write_text('{"failed": "false"}')
        self.assertEqual(health.check_health('backup'), 2)

    def test_normal_container_stop_is_not_an_incident_and_retry_does_not_clear_one(self) -> None:
        record = self.state / 'recovery.json'
        record.write_text('unreadable recovery evidence')
        self.unit['ActiveState'] = 'activating'
        self.assertEqual(health.check_health('recovery'), 0)
        self.unit['ActiveState'] = 'inactive'
        self.assertEqual(health.check_health('recovery'), 1)
        self.unit['ActiveState'] = 'activating'
        self.assertEqual(health.check_health('recovery'), 1)
        self.assertEqual(record.read_text(), 'unreadable recovery evidence')
        record.unlink()
        self.assertEqual(health.check_health('recovery'), 0)

    def test_dangling_recovery_record_is_not_ignored(self) -> None:
        (self.state / 'recovery.json').symlink_to(self.state / 'missing')
        self.assertEqual(health.check_health('recovery'), 1)

    def test_auth_and_daemon_outages_have_distinct_incidents(self) -> None:
        with patch.object(
            health,
            'request',
            return_value=[{'enable': True, 'state': 'signed-in'}, {'enable': True, 'state': 'signed-out'}],
        ) as request:
            self.assertEqual(health.check_health('auth'), 1)
            self.assertEqual(health.check_health('service'), 0)
            request.assert_called_with('status', timeout=5)
        with patch.object(health, 'request', side_effect=OSError('secret response')):
            self.assertEqual(health.check_health('service'), 1)
            self.assertEqual(health.check_health('auth'), 1)
        with patch.object(health, 'request', return_value=[{'enable': True, 'state': 'signed-in'}]):
            self.assertEqual(health.check_health('auth'), 0)
            self.assertEqual(health.check_health('service'), 0)

    def test_observation_failure_does_not_report_recovery_or_leak_details(self) -> None:
        output = io.StringIO()
        with (
            patch.object(health, 'unit_status', side_effect=BackupError('secret diagnostic')),
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(health.check_health('backup'), 2)
        self.assertNotIn('secret', output.getvalue())
        self.assertIn('could not complete', output.getvalue())
        self.unit['ActiveState'] = 'activating'
        self.assertEqual(health.check_health('backup'), 1)
        self.unit['ActiveState'] = 'inactive'
        # Missing status after a reboot cannot clear a previously observed failure.
        self.assertEqual(health.check_health('backup'), 1)
        self.status('completed')
        # The failed check had no baseline. Do not mistake an old completed file
        # restored by an administrator for a new successful backup.
        self.assertEqual(health.check_health('backup'), 1)
        self.status('completed', '2026-10-07T09:00:00+00:00')
        self.assertEqual(health.check_health('backup'), 0)

    def test_unchanged_observation_does_not_rewrite_the_cache(self) -> None:
        self.status('failed')
        self.assertEqual(health.check_health('backup'), 1)
        path = self.state / 'health-backup.json'
        before = path.stat().st_mtime_ns
        self.assertEqual(health.check_health('backup'), 1)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(decode(path.read_text()), {'failed': True, 'success': '', 'generation': None})

    def test_systemctl_is_bounded_and_incomplete_output_is_rejected(self) -> None:
        # Test the real parser separately from the observation fixture.
        self.stack.close()
        with patch.object(health.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '')) as run:
            with self.assertRaises(BackupError):
                health.unit_status('omv-protondrive-backup.service')
            self.assertEqual(run.call_args.kwargs['timeout'], 5)
            self.assertTrue(run.call_args.kwargs['check'])


if __name__ == '__main__':
    unittest.main()
