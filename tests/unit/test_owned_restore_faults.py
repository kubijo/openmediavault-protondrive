"""Extraction fault ownership survives partial setup and refuses unrelated state."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.integration import owned_restore_faults as faults


class RestoreFaultTests(unittest.TestCase):
    def test_failed_restart_leaves_owned_state_that_can_be_cleaned_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'token'
            directory.mkdir()
            override = root / 'unit.d/override.conf'
            with (
                patch.object(faults, 'STATE', root),
                patch.object(faults, 'OVERRIDE', override),
                patch.object(faults, 'run'),
                patch.object(faults, 'restart', side_effect=RuntimeError('restart failed')),
                self.assertRaisesRegex(RuntimeError, 'restart failed'),
            ):
                faults.arm(directory)
            self.assertEqual((root / 'active').read_text(), 'token\n')
            self.assertEqual(override.read_bytes(), faults.TEMPLATE.read_bytes())
            with (
                patch.object(faults, 'STATE', root),
                patch.object(faults, 'OVERRIDE', override),
                patch.object(faults, 'run'),
                patch.object(faults, 'restart'),
            ):
                self.assertEqual(faults.disarm(directory), {'disarmed': True})
                self.assertEqual(faults.disarm(directory), {'disarmed': True})
            self.assertFalse(override.exists())
            self.assertFalse((root / 'active').exists())

    def test_cleanup_does_not_remove_another_probes_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'token'
            directory.mkdir()
            (root / 'active').write_text('other\n')
            override = root / 'override.conf'
            override.write_text('unrelated')
            with patch.object(faults, 'OVERRIDE', override), self.assertRaisesRegex(RuntimeError, 'Another probe'):
                faults.disarm(directory)
            self.assertEqual(override.read_text(), 'unrelated')
            (root / 'active').write_text('token\n')
            with (
                patch.object(faults, 'STATE', root),
                patch.object(faults, 'OVERRIDE', override),
                self.assertRaisesRegex(RuntimeError, 'override changed'),
            ):
                faults.disarm(directory)
            self.assertEqual(override.read_text(), 'unrelated')

    def test_interrupted_extraction_cannot_leave_publication_or_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertEqual(faults.verify_absent(directory), {'absent': True})
            staging = directory / '.protondrive-extract-owned'
            staging.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'staging'):
                faults.verify_absent(directory)
            staging.rmdir()
            (directory / 'files').symlink_to(directory / 'missing')
            with self.assertRaisesRegex(RuntimeError, 'destination'):
                faults.verify_absent(directory)


if __name__ == '__main__':
    unittest.main()
