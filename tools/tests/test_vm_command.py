"""The host command bridge uses saved VM state and waits for one complete RPC response."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import vm_command
import vm_runtime
from tool_data import string
from vm_control import VMState


class VMCommandTests(unittest.TestCase):
    def test_host_flow_lease_excludes_concurrent_probe_even_without_a_guest_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = VMState(Path(temporary))
            args = ['--state-dir', temporary, 'flow-lease']
            with state.lock('live-flow'), self.assertRaisesRegex(RuntimeError, 'already active'):
                vm_command.main(args)
            with (
                patch.object(vm_command, 'guest_for', side_effect=AssertionError('must not connect')),
                patch.object(vm_command.sys, 'stdin', io.StringIO('')),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(vm_command.main(args), 0)
            self.assertEqual(json.loads(output.getvalue()), {'locked': True})

    def test_crash_reboot_requires_guest_proof_before_resetting_vm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            guest = Mock(run=Mock(side_effect=RuntimeError('not a development fixture')))
            with patch.object(vm_command, 'qmp') as qmp, self.assertRaisesRegex(RuntimeError, 'fixture'):
                vm_command.crash_reboot(Path(temporary), guest)
            qmp.assert_not_called()

    def test_crash_reboot_waits_for_new_boot_and_services(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            responses = [
                Mock(stdout='{"boot_id":"old"}'),
                Mock(returncode=255, stdout=''),
                Mock(returncode=0, stdout='old'),
                Mock(returncode=0, stdout='new'),
                Mock(returncode=0, stdout='active\n\nactivating\n\nfailed\n'),
                Mock(returncode=0, stdout='new'),
                Mock(returncode=0, stdout='active\n\nactive\n\nactive\n'),
            ]
            run = Mock(side_effect=responses)
            guest = Mock(run=run)
            with patch.object(vm_command, 'qmp') as qmp, patch.object(vm_command.time, 'sleep'):
                self.assertTrue(vm_command.crash_reboot(Path(temporary), guest)['rebooted'])
            qmp.assert_called_once_with(Path(temporary) / 'control.sock', 'system_reset')
            self.assertEqual(run.call_count, len(responses))

    def test_crash_reboot_rejects_missing_failed_or_inactive_services(self) -> None:
        for output, returncode in (
            ('active\nactive\n', 0),
            ('active\nactive\nfailed\n', 0),
            ('active\ninactive\nactive\n', 0),
            ('active\nactive\nactive\n', 1),
        ):
            with self.subTest(output=output, returncode=returncode), tempfile.TemporaryDirectory() as temporary:
                guest = Mock(
                    run=Mock(
                        side_effect=[
                            Mock(stdout='{"boot_id":"old"}'),
                            Mock(returncode=0, stdout='new'),
                            Mock(returncode=returncode, stdout=output),
                        ]
                    )
                )
                with (
                    patch.object(vm_command, 'qmp'),
                    patch.object(vm_command.time, 'monotonic', side_effect=[0, 1, 181]),
                    patch.object(vm_command.time, 'sleep'),
                    self.assertRaisesRegex(RuntimeError, 'new boot ID'),
                ):
                    vm_command.crash_reboot(Path(temporary), guest)

    def test_crash_reboot_never_reports_success_for_unchanged_boot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            guest = Mock(run=Mock(side_effect=[Mock(stdout='{"boot_id":"old"}'), Mock(returncode=0, stdout='old')]))
            with (
                patch.object(vm_command, 'qmp'),
                patch.object(vm_command.time, 'monotonic', side_effect=[0, 1, 181]),
                patch.object(vm_command.time, 'sleep'),
                self.assertRaisesRegex(RuntimeError, 'new boot ID'),
            ):
                vm_command.crash_reboot(Path(temporary), guest)

    def test_guest_for_uses_saved_port_and_refuses_stopped_vm(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = VMState(Path(temporary))
            state.instance.mkdir()
            state.write({'ssh_port': 2244})
            with (
                patch.object(VMState, 'running', return_value=None),
                self.assertRaisesRegex(RuntimeError, 'stopped'),
            ):
                vm_command.guest_for(state.root)
            with patch.object(VMState, 'running', return_value={'status': 'running'}):
                guest = vm_command.guest_for(state.root)
            self.assertIn('2244', guest.ssh)
            self.assertIn(str(state.instance / 'key'), guest.ssh)

    def test_exec_preserves_argv_and_exit_status(self):
        guest = vm_runtime.Guest(['ssh', 'guest'], [], Path('/unused'), None)
        with patch.object(vm_runtime, 'run', return_value=Mock(returncode=37)) as run:
            self.assertEqual(vm_command.execute(guest, ['--', '--', 'printf', '%s', 'two words']), 37)
        run.assert_called_once_with('ssh', 'guest', "printf %s 'two words'", stdin=subprocess.DEVNULL, check=False)
        with self.assertRaisesRegex(ValueError, 'Supply a guest command'):
            vm_command.execute(guest, ['--'])

    def test_rpc_sends_one_line_and_waits_for_complete_result(self):
        run = Mock()
        guest = Mock(run=run)
        run.return_value = Mock(returncode=0, stdout='{"ok":true,"result":[{"name":"archive"}]}\n')
        self.assertEqual(vm_command.rpc(guest, 'prepare', 'set-uuid'), [{'name': 'archive'}])
        assert run.call_args is not None
        args = cast(tuple[object, ...], run.call_args.args)
        kwargs = cast(dict[str, object], run.call_args.kwargs)
        self.assertEqual(args, ('socat', 'STDIO,ignoreeof', 'UNIX-CONNECT:/run/omv-protondrive/control.sock'))
        self.assertEqual(json.loads(string(kwargs['input'])), {'operation': 'prepare', 'setuuid': 'set-uuid'})
        self.assertTrue(string(kwargs['input']).endswith('\n'))
        self.assertTrue(kwargs['capture_output'])

    def test_exec_stdin_is_forwarded_only_when_explicitly_requested(self):
        run = Mock()
        guest = Mock(run=run)
        run.return_value = Mock(returncode=0)
        self.assertEqual(vm_command.execute(guest, ['cat'], stdin=True), 0)
        run.assert_called_once_with('cat', stdin=None, check=False)

    def test_rpc_rejects_errors_without_claiming_success(self):
        run = Mock()
        guest = Mock(run=run)
        with self.assertRaisesRegex(ValueError, 'requires --set-uuid'):
            vm_command.rpc(guest, 'prepare')
        run.assert_not_called()
        run.return_value = Mock(returncode=0, stdout='{"ok":false,"error":"Unknown backup set"}\n')
        with self.assertRaisesRegex(RuntimeError, 'Unknown backup set'):
            vm_command.rpc(guest, 'prepare', 'missing')
        run.return_value = Mock(returncode=0, stdout='')
        with self.assertRaisesRegex(RuntimeError, 'Missing or oversized'):
            vm_command.rpc(guest, 'status')
        run.return_value = Mock(returncode=255, stdout='')
        with self.assertRaisesRegex(RuntimeError, 'transport failed'):
            vm_command.rpc(guest, 'status')


if __name__ == '__main__':
    unittest.main()
