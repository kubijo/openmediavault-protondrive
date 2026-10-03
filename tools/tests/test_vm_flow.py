"""Failure reporting and cleanup order for the unattended VM flow."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import vm_flow


class VMFlowTests(unittest.TestCase):
    def run_flow(self, *, same_reset=False, fail_backup=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'justfile').touch()
            binary = root / 'bin'
            binary.mkdir()
            just = binary / 'just'
            just.write_text(
                f'#!{sys.executable}\n'
                'import json, os, sys\n'
                'args = sys.argv[1:]\n'
                'if args[0] == "vm::probe":\n'
                '    name = args[args.index("--output") + 1].split("/")[-1]\n'
                '    remote = "initial" if name != "reset-web" or os.getenv("SAME_RESET") else "new"\n'
                '    print(json.dumps({"ok": True, "remote_folder": remote, "screenshots": []}))\n'
                'elif args[0] == "test::vm" and os.getenv("FAIL_BACKUP"):\n'
                '    sys.exit(1)\n'
            )
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
            [report_path] = (root / '.tmp/autonomous-flow').glob('*/report.json')
            return process, json.loads(report_path.read_text())

    def test_reset_requires_a_new_web_identity(self):
        process, report = self.run_flow(same_reset=True)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report['ok'])
        self.assertIn('retained the previous remote folder', report['error'])
        self.assertNotIn('backup-restore', [step['name'] for step in report['steps']])

    def test_backup_failure_skips_disk_delete_and_records_error(self):
        process, report = self.run_flow(fail_backup=True)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report['ok'])
        self.assertIn('backup-restore failed', report['error'])
        names = [step['name'] for step in report['steps']]
        self.assertIn('stop-after-failure', names)
        self.assertNotIn('delete-disposable-disk', names)

    def test_success_deletes_disk_only_after_backup_restore(self):
        process, report = self.run_flow()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(report['ok'])
        self.assertNotEqual(report['remote_folders']['initial'], report['remote_folders']['reset'])
        names = [step['name'] for step in report['steps']]
        self.assertLess(names.index('backup-restore'), names.index('delete-disposable-disk'))


if __name__ == '__main__':
    unittest.main()
