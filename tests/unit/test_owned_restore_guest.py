"""The browser fixture helper must never clean unrelated guest paths."""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.integration import owned_restore_guest as guest


class OwnedRestoreFixtureTests(unittest.TestCase):
    def test_cli_keeps_positional_arguments_and_dispatches_typed_actions(self) -> None:
        token = str(uuid4())
        for action in ('prepare', 'verify', 'cleanup'):
            with (
                self.subTest(action=action),
                patch('sys.argv', ['owned_restore_guest.py', action, token]),
                patch.object(guest, 'load'),
                patch.object(guest, 'guard') as guard,
                patch.object(guest, action, return_value={'ok': True}) as operation,
                redirect_stdout(io.StringIO()) as output,
            ):
                guest.main()
                guard.assert_called_once()
                operation.assert_called_once_with(token)
                self.assertEqual(output.getvalue(), '{"ok": true}\n')

    def test_invalid_cli_input_and_help_never_touch_guest_state(self) -> None:
        for arguments, code in [(['--help'], 0), (['invalid', str(uuid4())], 2), (['prepare'], 2)]:
            with (
                self.subTest(arguments=arguments),
                patch('sys.argv', ['owned_restore_guest.py', *arguments]),
                patch.object(guest, 'load') as load,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                guest.main()
            self.assertEqual(error.exception.code, code)
            load.assert_not_called()

    def test_fixture_verification_and_owned_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.write_text('expected')
            with (
                patch.object(guest, 'ROOT', root / 'fixtures'),
                patch.object(guest, 'SOURCE', source),
                patch.object(guest, 'STATE', root),
            ):
                token = str(uuid4())
                result = guest.prepare(token)
                restored = Path(result['destination']) / source.relative_to('/')
                restored.parent.mkdir(parents=True)
                restored.write_text('expected')
                self.assertEqual(guest.verify(token), {'verified': True})
                restored.write_text('damaged')
                with self.assertRaisesRegex(RuntimeError, 'differs'):
                    guest.verify(token)
                self.assertEqual(guest.cleanup(token), {'cleaned': True})
                self.assertFalse((guest.ROOT / token).exists())

    def test_symlink_parent_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / 'outside'
            outside.mkdir()
            link = root / 'alias'
            link.symlink_to(outside, target_is_directory=True)
            with patch.object(guest, 'ROOT', link), self.assertRaises(RuntimeError):
                guest.prepare(str(uuid4()))
            self.assertEqual(list(outside.iterdir()), [])
