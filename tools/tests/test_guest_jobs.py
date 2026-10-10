"""Remote jobs must be stopped and acknowledged before installation can be retried."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import vm_runtime
from test_interactive_vm import OutputMock


class GuestJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guest = vm_runtime.guest_connection(self.root, self.root / 'key', 2222)

    def test_cancellation_is_confirmed_in_both_output_modes(self):
        for animate in (False, True):
            for failure in (subprocess.TimeoutExpired('ssh', 1), KeyboardInterrupt()):
                with self.subTest(animate=animate, failure=type(failure).__name__):
                    output = OutputMock(animate=animate)
                    output.run_mock.side_effect = failure
                    with (
                        patch.object(self.guest, 'run', return_value=Mock(stdout='inactive\n')) as remote,
                        self.assertRaises(type(failure)),
                    ):
                        self.guest.python(output, '/root/fixture.py', self.root / 'log', 1)
                    self.assertEqual(remote.call_args_list[0].args[:2], ('systemctl', 'stop'))
                    self.assertEqual(remote.call_args_list[1].args[:2], ('systemctl', 'show'))
                    self.assertFalse(self.guest.job_marker.exists())

    def test_unconfirmed_cancellation_blocks_retry(self):
        output = OutputMock(animate=False)
        output.run_mock.side_effect = subprocess.TimeoutExpired('ssh', 1)
        with patch.object(self.guest, 'run', return_value=Mock(stdout='deactivating\n')):
            with self.assertRaisesRegex(RuntimeError, 'has not stopped'):
                self.guest.python(output, '/root/fixture.py', self.root / 'log', 1)
            self.assertTrue(self.guest.job_marker.exists())
            with self.assertRaisesRegex(RuntimeError, 'has not stopped'):
                self.guest.python(output, '/root/fixture.py', self.root / 'log', 1)
        output.run_mock.assert_called_once()
        # Reconnection confirms the old job has stopped before admitting another.
        output.run_mock.side_effect = None
        with patch.object(self.guest, 'run', return_value=Mock(stdout='inactive\n')):
            self.guest.python(output, '/root/fixture.py', self.root / 'log', 1)
        self.assertEqual(output.run_mock.call_count, 2)
        self.assertFalse(self.guest.job_marker.exists())

    def test_lost_ssh_connection_keeps_pending_job_marker(self):
        self.guest.job_marker.touch()
        with (
            patch.object(self.guest, 'run', side_effect=subprocess.CalledProcessError(255, 'ssh')),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.guest.ensure_idle()
        self.assertTrue(self.guest.job_marker.exists())
