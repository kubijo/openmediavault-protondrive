"""Failure reporting and cleanup order for the unattended VM flow."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import vm_flow
from tool_data import decode, items, mapping, string


class VMFlowTests(unittest.TestCase):
    def run_flow(
        self, *, same_reset: bool = False, fail_backup: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'justfile').touch()
            binary = root / 'bin'
            binary.mkdir()
            just = binary / 'just'
            shutil.copyfile(Path(__file__).parent / 'fixtures/fake_just_vm_flow.py', just)
            just.chmod(0o755)
            environment = os.environ.copy()
            environment['PATH'] = f'{binary}:{environment["PATH"]}'
            environment['SAME_RESET'] = '1' if same_reset else ''
            environment['FAIL_BACKUP'] = '1' if fail_backup else ''
            process = subprocess.run(
                [sys.executable, vm_flow.__file__],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            reports = list((root / '.tmp/autonomous-flow').glob('*/report.json'))
            self.assertEqual(len(reports), 1, (process.returncode, process.stdout, process.stderr))
            [report_path] = reports
            return process, decode(report_path.read_text())

    def test_reset_requires_a_new_web_identity(self):
        process, report = self.run_flow(same_reset=True)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report['ok'])
        self.assertIn('new instance UUID', string(report['error']))
        self.assertNotIn('backup-restore', [string(mapping(step)['name']) for step in items(report['steps'])])

    def test_backup_failure_skips_disk_delete_and_records_error(self):
        process, report = self.run_flow(fail_backup=True)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report['ok'])
        self.assertIn('backup-restore failed', string(report['error']))
        names = [string(mapping(step)['name']) for step in items(report['steps'])]
        self.assertIn('stop-after-failure', names)
        self.assertNotIn('delete-disposable-disk', names)

    def test_success_deletes_disk_only_after_backup_restore(self):
        process, report = self.run_flow()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(report['ok'])
        self.assertEqual(mapping(report['remote_folders'])['initial'], mapping(report['remote_folders'])['reset'])
        self.assertNotEqual(mapping(report['instance_uuids'])['initial'], mapping(report['instance_uuids'])['reset'])
        names = [string(mapping(step)['name']) for step in items(report['steps'])]
        self.assertLess(names.index('backup-restore'), names.index('delete-disposable-disk'))


if __name__ == '__main__':
    unittest.main()
