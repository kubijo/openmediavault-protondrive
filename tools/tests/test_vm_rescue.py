"""Disk rescue must select only this VM and resume QEMU after every copy failure."""

import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from vm_rescue import FILES, deleted_files, suspended


class RescueTests(unittest.TestCase):
    def test_exact_deleted_instance_paths_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptors = root / 'fd'
            descriptors.mkdir()
            instance = root / 'instance'
            for number, name in enumerate(FILES):
                (descriptors / str(number)).symlink_to(f'{instance / name} (deleted)')
            (descriptors / 'unrelated').symlink_to('/unrelated/disk.qcow2 (deleted)')
            self.assertEqual(set(deleted_files(root, instance)), set(FILES))
            (descriptors / '0').unlink()
            with self.assertRaisesRegex(RuntimeError, 'all three'):
                deleted_files(root, instance)

    def test_copy_failure_always_resumes_the_same_pidfd(self) -> None:
        with patch('vm_rescue.process_state', return_value='T'), patch('signal.pidfd_send_signal') as send:
            with self.assertRaisesRegex(OSError, 'copy failed'), suspended(42, Path('/unused')):
                raise OSError('copy failed')
            self.assertEqual(send.call_args_list, [call(42, signal.SIGSTOP), call(42, signal.SIGCONT)])

    def test_suspend_timeout_still_sends_resume(self) -> None:
        with (
            patch('vm_rescue.process_state', return_value='R'),
            patch('vm_rescue.time.monotonic', side_effect=[0, 6]),
            patch('signal.pidfd_send_signal') as send,
            self.assertRaisesRegex(RuntimeError, 'did not suspend'),
            suspended(42, Path('/unused')),
        ):
            self.fail('Copying before QEMU stops is unsafe')
        self.assertEqual(send.call_args_list, [call(42, signal.SIGSTOP), call(42, signal.SIGCONT)])
