"""The host command bridge uses saved VM state and waits for one complete RPC response."""

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
