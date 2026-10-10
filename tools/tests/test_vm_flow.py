"""Regression orchestration, cancellation, failure reporting and mandatory teardown."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rich.console import Console

import vm_flow
from tool_data import decode, items, mapping, string


class VMFlowTests(unittest.TestCase):
    def test_live_view_renders_steps_and_current_vm_stage(self):
        stream = io.StringIO()
        console = Console(file=stream, width=80, force_terminal=True, color_system='256', no_color=False, record=True)
        view = vm_flow.FlowDisplay(Path('/tmp/regression'), console=console, animate=True)
        view.begin('init')
        view.progress('init', 'Boot VM and wait for SSH')
        console.print(view.render())
        output = console.export_text()
        self.assertIn('┌', output)
        initial_width = len(next(line for line in output.splitlines() if line.startswith('┌')))
        self.assertLess(initial_width, 50)
        header = next(line for line in output.splitlines() if 'Step' in line and 'Status' in line)
        self.assertLess(header.index('Step'), header.index('Status'))
        self.assertLess(header.index('Status'), header.index('Time'))
        self.assertIn('Boot VM and wait for SSH', output)
        self.assertIn('RUNNING', output)
        self.assertRegex(output, r'│ init │ \S RUNNING\s+│')
        view.finish('init', 0)
        view.begin('initial-web')
        console.print(view.render())
        output = console.export_text()
        self.assertIn('PASS', output)
        self.assertGreater(len(next(line for line in output.splitlines() if line.startswith('┌'))), initial_width)
        self.assertIn('\x1b[48;5;', stream.getvalue())
        self.assertIn('\x1b]8;', stream.getvalue())
        self.assertIn('file:///tmp/regression/init.log', stream.getvalue())

        plain = io.StringIO()
        plain_console = Console(file=plain, width=80, force_terminal=False, color_system=None)
        plain_view = vm_flow.FlowDisplay(Path('/tmp/regression'), console=plain_console, animate=False)
        plain_view.open()
        self.assertIn('/tmp/regression', plain.getvalue())
        self.assertNotIn('\x1b]8;', plain.getvalue())

        wide = io.StringIO()
        wide_console = Console(file=wide, width=160, height=40, force_terminal=True, no_color=True)
        wide_view = vm_flow.FlowDisplay(Path('/tmp/regression'), console=wide_console, animate=True)
        wide_view.begin('very-long-step-' * 12)
        wide_console.print(wide_view.render())
        self.assertEqual(len(next(line for line in wide.getvalue().splitlines() if line.startswith('┌'))), 120)
        self.assertNotIn('\x1b]8;', wide.getvalue())

    def test_live_view_replaces_spinner_with_final_table(self):
        stream = io.StringIO()
        console = Console(file=stream, width=80, force_terminal=True, no_color=False)
        view = vm_flow.FlowDisplay(Path('/tmp/regression'), console=console, animate=True)
        view.open()
        view.begin('init')
        view.progress('init', 'Boot VM and wait for SSH')
        view.finish('init', 0)
        view.close()
        self.assertIsNone(view.live)
        self.assertIn('PASS', stream.getvalue())
        self.assertIn('Artifacts:', stream.getvalue())

    def test_live_view_shows_all_steps(self):
        stream = io.StringIO()
        console = Console(file=stream, width=80, height=40, force_terminal=False)
        view = vm_flow.FlowDisplay(Path('/tmp/regression'), console=console, animate=True)
        view.steps = [vm_flow.StepView(f'step-{index:02}', started=0, elapsed=1, exit=0) for index in range(15)]
        console.print(view.render())
        output = stream.getvalue()
        self.assertIn('step-00', output)
        self.assertIn('step-14', output)
        self.assertEqual(output.count('PASS'), 15)
        self.assertNotIn('earlier', output)

    def run_flow(
        self, *, keep_failed: bool = False, **faults: str
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / 'bin'
            binary.mkdir()
            arguments: list[str] = []
            for name in ('vm-up', 'vm-control', 'vm-command', 'web-probe', 'test-vm'):
                executable = binary / name
                shutil.copyfile(Path(__file__).parent / 'fixtures/fake_just_vm_flow.py', executable)
                executable.chmod(0o755)
                arguments.extend((f'--{name}', str(executable)))
            process = subprocess.run(
                [
                    sys.executable,
                    vm_flow.__file__,
                    *arguments,
                    '--timeout',
                    '2',
                    *(['--keep-failed'] if keep_failed else []),
                ],
                cwd=root,
                env={**os.environ, **faults},
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            reports = list((root / '.tmp/regression').glob('*/report.json'))
            self.assertEqual(len(reports), 1, (process.returncode, process.stdout, process.stderr))
            [report_path] = reports
            self.assertEqual(
                (report_path.parent / 'vm/running').exists(), bool(faults.get('FAIL_FORCE_STOP')), process.stderr
            )
            return process, decode(report_path.read_text())

    def names(self, report: dict[str, object]) -> list[str]:
        return [string(mapping(step)['name']) for step in items(report['steps'])]

    def test_reset_requires_a_new_identity_and_always_tears_down(self):
        process, report = self.run_flow(SAME_RESET='1')
        self.assertEqual(process.returncode, 1)
        self.assertIn('new instance UUID', string(report['error']))
        self.assertNotIn('service-regression', self.names(report))
        self.assertEqual(self.names(report)[-2:], ['teardown-stop', 'teardown-delete'])

    def test_failure_stops_and_deletes_by_default(self):
        process, report = self.run_flow(FAIL_BACKUP='1')
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report['ok'])
        self.assertIn('service-regression failed', string(report['error']))
        self.assertEqual(self.names(report)[-2:], ['teardown-stop', 'teardown-delete'])

    def test_keep_failed_preserves_disk_but_stops_vm(self):
        process, report = self.run_flow(keep_failed=True, FAIL_BACKUP='1')
        self.assertEqual(process.returncode, 1)
        self.assertEqual(self.names(report)[-1], 'teardown-stop')
        self.assertNotIn('teardown-delete', self.names(report))

    def test_failed_initialization_still_shuts_down_with_force_fallback(self):
        process, report = self.run_flow(FAIL_INIT='1', FAIL_STOP='1')
        self.assertEqual(process.returncode, 1)
        self.assertEqual(self.names(report), ['init', 'teardown-stop', 'teardown-force-stop', 'teardown-delete'])

    def test_sigterm_records_interrupted_step_and_tears_down(self):
        process, report = self.run_flow(INTERRUPT_INIT='1')
        self.assertEqual(process.returncode, 143, process.stderr)
        self.assertEqual(self.names(report), ['init', 'teardown-stop', 'teardown-delete'])
        self.assertFalse(report['ok'])

    def test_browser_must_report_success_even_with_zero_process_exit(self):
        process, report = self.run_flow(BAD_PROBE='1')
        self.assertEqual(process.returncode, 1)
        self.assertIn('no successful browser result', string(report['error']))
        self.assertEqual(self.names(report)[-2:], ['teardown-stop', 'teardown-delete'])

    def test_timeout_stops_process_and_owned_vm(self):
        process, report = self.run_flow(TIMEOUT_INIT='1')
        self.assertEqual(process.returncode, 1)
        self.assertEqual(mapping(items(report['steps'])[0])['exit'], 124)
        self.assertEqual(self.names(report)[-2:], ['teardown-stop', 'teardown-delete'])

    def test_signal_during_teardown_retries_cleanup(self):
        process, report = self.run_flow(FAIL_INIT='1', INTERRUPT_TEARDOWN='1')
        self.assertEqual(process.returncode, 143, process.stderr)
        self.assertEqual(self.names(report)[-2:], ['teardown-stop', 'teardown-delete'])

    def test_failed_cleanup_retains_original_failure_and_does_not_delete_disk(self):
        process, report = self.run_flow(FAIL_INIT='1', FAIL_FORCE_STOP='1')
        self.assertEqual(process.returncode, 1)
        self.assertIn('init failed', string(report['error']))
        self.assertIn('teardown-force-stop failed', string(report['cleanup_error']))
        self.assertNotIn('teardown-delete', self.names(report))

    def test_success_covers_owned_ui_metadata_and_lifecycle_before_deleting(self):
        process, report = self.run_flow()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('init: Prepare disposable VM', process.stdout)
        self.assertTrue(report['ok'])
        self.assertNotEqual(mapping(report['instance_uuids'])['initial'], mapping(report['instance_uuids'])['reset'])
        names = self.names(report)
        for name in (
            'owned-layout',
            'owned-edits',
            'owned-backup-restore',
            'failure-reporting',
            'restore-metadata',
            'restore',
            'reset',
        ):
            self.assertIn(name, names)
        self.assertLess(names.index('service-regression'), names.index('teardown-delete'))
        steps = {string(mapping(step)['name']): mapping(step) for step in items(report['steps'])}
        self.assertIn('--owned-ui-backup', items(steps['owned-backup-restore']['command']))
        self.assertIn('--owned-ui-restore', items(steps['owned-backup-restore']['command']))
        self.assertIn('--owned-ui-resilience', items(steps['owned-backup-restore']['command']))
        self.assertIn('--owned-ui-foreign', items(steps['owned-backup-restore']['command']))
        self.assertIn('--owned-ui-interrupt', items(steps['owned-interrupt']['command']))
        self.assertIn('--recover', items(steps['owned-recover']['command']))
        self.assertIn('--regression', items(steps['init']['command']))


if __name__ == '__main__':
    unittest.main()
