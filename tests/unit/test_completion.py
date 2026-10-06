"""Successful-run ordering must survive wall-clock changes and reject damaged records."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from protondrive.common import BackupError, atomic_json, locked
from protondrive.completion import Completion, read_completion, record_completion
from protondrive.json_data import decode


class CompletionTests(unittest.TestCase):
    def test_completion_waits_for_health_and_uses_its_latest_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor() as executor:
            state = Path(directory)
            started = threading.Event()
            timestamp = '2026-10-06T09:00:00+00:00'

            def complete() -> None:
                started.set()
                record_completion(state, timestamp)

            with locked(state / 'health-backup.lock'):
                future = executor.submit(complete)
                self.assertTrue(started.wait(timeout=2))
                atomic_json(state / 'health-backup.json', {'failed': True, 'success': timestamp, 'generation': 7})
                self.assertFalse(future.done())
            future.result(timeout=2)
            self.assertEqual(read_completion(state), Completion(8, timestamp))

    def test_interrupted_migration_retains_incident_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            timestamp = '2026-10-06T09:00:00+00:00'
            health = state / 'health-backup.json'
            original = {'failed': True, 'success': '2026-10-07T09:00:00+00:00', 'generation': None}
            atomic_json(health, original)

            def interrupted_write(path: Path, value: object) -> None:
                if path.name == 'backup-completion.json':
                    raise OSError('Interrupted completion write')
                atomic_json(path, value)

            with (
                patch('protondrive.completion.atomic_json', side_effect=interrupted_write),
                self.assertRaises(OSError),
            ):
                record_completion(state, timestamp)
            self.assertIsNone(read_completion(state))
            self.assertEqual(decode(health.read_text()), {**original, 'generation': 0})
            record_completion(state, timestamp)
            self.assertEqual(read_completion(state), Completion(1, timestamp))
            self.assertEqual(decode(health.read_text()), {**original, 'generation': 0})

    def test_invalid_health_counter_is_not_used_or_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / 'health-backup.json'
            for generation in (True, -1, '3'):
                atomic_json(path, {'failed': True, 'success': '', 'generation': generation})
                original = path.read_bytes()
                with self.assertRaises(BackupError):
                    record_completion(state, '2026-10-06T09:00:00+00:00')
                self.assertEqual(path.read_bytes(), original)
                self.assertIsNone(read_completion(state))
            path.unlink()
            path.symlink_to(state / 'absent')
            with self.assertRaises(BackupError):
                record_completion(state, '2026-10-06T09:00:00+00:00')
            self.assertTrue(path.is_symlink())

    def test_only_explicit_completion_advances_the_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.assertIsNone(read_completion(state))
            first = '2026-10-06T09:00:00+00:00'
            record_completion(state, first)
            self.assertEqual(read_completion(state), Completion(1, first))
            second = '2026-10-05T09:00:00+00:00'
            record_completion(state, second)
            self.assertEqual(read_completion(state), Completion(2, second))
            self.assertEqual(read_completion(state), Completion(2, second))

    def test_invalid_records_are_retained_instead_of_resetting_the_counter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / 'backup-completion.json'
            for value in (
                {'generation': True, 'timestamp': '2026-10-06T09:00:00+00:00'},
                {'generation': 0, 'timestamp': '2026-10-06T09:00:00+00:00'},
                {'generation': 1, 'timestamp': '2026-10-06T09:00:00'},
            ):
                atomic_json(path, value)
                before = path.read_bytes()
                with self.assertRaises(BackupError):
                    record_completion(state, '2026-10-07T09:00:00+00:00')
                self.assertEqual(path.read_bytes(), before)
            path.unlink()
            path.symlink_to(state / 'absent')
            with self.assertRaises(BackupError):
                record_completion(state, '2026-10-07T09:00:00+00:00')
            self.assertTrue(path.is_symlink())
