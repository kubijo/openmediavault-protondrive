import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protondrive.common import BackupError
from protondrive.json_data import decode, object_value
from protondrive.recovery import Recovery

A, B = 'a' * 64, 'b' * 64


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.running = {A: True, B: True, 'c' * 64: False}
        self.calls: list[tuple[str, ...]] = []

    def docker(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] == 'ps':
            return A + '\n' + B + '\n'
        if args[0] == 'inspect':
            return str(self.running[args[-1]]).lower()
        if args[0] == 'stop':
            self.assertEqual(object_value(decode((self.state / 'recovery.json').read_text()))['ids'], [A, B])
            self.running[args[-1]] = False
        if args[0] == 'start':
            self.running[args[-1]] = True
        return ''

    def test_symlinked_recovery_evidence_is_rejected_and_preserved(self) -> None:
        record = self.state / 'recovery.json'
        target = self.state / 'elsewhere.json'
        for content in (None, '{"ids": []}'):
            if content is not None:
                target.write_text(content)
            record.symlink_to(target)
            with self.assertRaisesRegex(BackupError, 'symlink'):
                Recovery(self.state, self.docker).restore()
            self.assertTrue(record.is_symlink())
            self.assertFalse(self.calls)
            if content is not None:
                self.assertEqual(target.read_text(), content)
            record.unlink()

    def test_only_previously_running_containers_restarted(self):
        recovery = Recovery(self.state, self.docker)
        recovery.stop(120)
        self.assertNotIn('stop_deadline', object_value(decode(recovery.path.read_text())))
        self.assertFalse(self.running[A])
        recovery.restore()
        self.assertTrue(self.running[A])
        self.assertTrue(self.running[B])
        self.assertFalse(self.running['c' * 64])
        self.assertFalse(recovery.path.exists())

    def test_partial_stop_failure_is_recoverable_by_new_process(self):
        def fail(*args: str) -> str:
            if args[0] == 'stop' and args[-1] == B:
                raise BackupError('Docker unavailable')
            return self.docker(*args)

        with self.assertRaises(BackupError):
            Recovery(self.state, fail).stop(1)
        with patch('protondrive.recovery.time.sleep') as wait:
            Recovery(self.state, self.docker).restore()
            wait.assert_called_once()
        self.assertTrue(self.running[A])
        self.assertTrue(self.running[B])

    def test_failed_restart_retains_only_unrecovered_ids(self):
        recovery = Recovery(self.state, self.docker)
        recovery.stop(1)

        def fail(*args: str) -> str:
            if args[0] == 'start' and args[-1] == B:
                raise BackupError('start failed')
            return self.docker(*args)

        with self.assertRaises(BackupError):
            Recovery(self.state, fail).restore()
        self.assertEqual(object_value(decode(recovery.path.read_text()))['ids'], [B])
        Recovery(self.state, self.docker).restore()
