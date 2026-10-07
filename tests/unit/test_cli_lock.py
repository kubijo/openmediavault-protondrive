"""Corrupt vendor locks are diagnosed and preserved without touching credentials."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import configuration
from protondrive.cli_lock import MESSAGE, inspect_lock
from protondrive.common import BackupError
from protondrive.protoncli import ProtonCli


class CliLockTests(unittest.TestCase):
    def test_corrupt_lock_is_preserved_privately_and_repair_is_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lock = directory / 'events.lock'
            lock.write_bytes(b'\0' * 13)
            credentials = directory / 'credentials'
            credentials.write_bytes(b'unchanged')
            self.assertEqual(inspect_lock(path=directory), MESSAGE)
            evidence = inspect_lock(path=directory, repair=True)
            self.assertIsNotNone(evidence)
            assert evidence is not None
            self.assertEqual(Path(evidence).read_bytes(), b'\0' * 13)
            self.assertEqual(Path(evidence).stat().st_mode & 0o777, 0o600)
            self.assertFalse(lock.exists())
            self.assertEqual(credentials.read_bytes(), b'unchanged')
            self.assertIsNone(inspect_lock(path=directory, repair=True))

    def test_valid_lock_and_unsafe_paths_are_never_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lock = directory / 'events.lock'
            lock.write_text('{"pid":123}')
            self.assertIsNone(inspect_lock(path=directory, repair=True))
            self.assertEqual(lock.read_text(), '{"pid":123}')
            target = directory / 'target'
            target.write_bytes(b'\0')
            lock.unlink()
            lock.symlink_to(target)
            with self.assertRaises(OSError):
                inspect_lock(path=directory, repair=True)
            self.assertTrue(lock.is_symlink())
            self.assertEqual(target.read_bytes(), b'\0')
            lock.unlink()
            os.link(target, lock)
            with self.assertRaises(BackupError):
                inspect_lock(path=directory, repair=True)
            self.assertTrue(lock.exists())

    def test_failed_evidence_sync_keeps_original_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lock = directory / 'events.lock'
            lock.write_bytes(b'\0')
            with (
                patch('protondrive.cli_lock.os.fsync', side_effect=OSError('disk failure')),
                self.assertRaises(OSError),
            ):
                inspect_lock(path=directory, repair=True)
            self.assertEqual(lock.read_bytes(), b'\0')

    def test_repair_refuses_active_login_without_reading_state(self) -> None:
        config, _ = configuration()
        cli = ProtonCli(config)
        cli.login = Mock()
        with patch('protondrive.protoncli.inspect_lock') as inspect:
            with self.assertRaisesRegex(BackupError, 'in progress'):
                cli.repair_lock()
            inspect.assert_not_called()

    def test_changed_lock_is_not_removed_after_evidence_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            lock = directory / 'events.lock'
            lock.write_bytes(b'\0')
            sync = os.fsync

            def replace_after_sync(descriptor: int) -> None:
                sync(descriptor)
                replacement = directory / 'replacement'
                replacement.write_text('{"pid":123}')
                replacement.replace(lock)

            with (
                patch('protondrive.cli_lock.os.fsync', side_effect=replace_after_sync),
                self.assertRaisesRegex(BackupError, 'changed'),
            ):
                inspect_lock(path=directory, repair=True)
            self.assertEqual(lock.read_text(), '{"pid":123}')
            [evidence] = directory.glob('events.lock.corrupt-*')
            self.assertEqual(evidence.read_bytes(), b'\0')

    def test_repair_refuses_concurrent_command_without_reading_state(self) -> None:
        config, _ = configuration()
        cli = ProtonCli(config)
        acquired, release = threading.Event(), threading.Event()

        def command() -> None:
            with cli.lock:
                acquired.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=command)
        worker.start()
        try:
            self.assertTrue(acquired.wait(timeout=2))
            with patch('protondrive.protoncli.inspect_lock') as inspect:
                with self.assertRaisesRegex(BackupError, 'in progress'):
                    cli.repair_lock()
                inspect.assert_not_called()
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_command_failure_reports_corrupt_lock_without_raw_diagnostics(self) -> None:
        config, _ = configuration()
        cli = ProtonCli(config)
        process = Mock(returncode=1, communicate=Mock(return_value=('', 'sensitive vendor output')))
        with (
            patch('protondrive.protoncli.subprocess.Popen', return_value=process),
            patch('protondrive.protoncli.inspect_lock', return_value=MESSAGE),
            self.assertRaisesRegex(BackupError, 'repair-cli-lock') as error,
        ):
            cli.info('/my-files')
        self.assertNotIn('sensitive', str(error.exception))


if __name__ == '__main__':
    unittest.main()
