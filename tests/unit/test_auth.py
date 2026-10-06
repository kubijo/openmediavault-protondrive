import contextlib
import io
import json
import shutil
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from helpers import configuration
from protondrive import cli as commands
from protondrive.common import BackupError, atomic_json, locked
from protondrive.json_data import decode, object_value
from protondrive.protoncli import ProtonCli


class AuthTests(unittest.TestCase):
    def test_backup_status_distinguishes_pending_recovery_from_a_running_backup(self) -> None:
        for running, record, phase, expected in (
            (False, True, 'archiving', 'recovery-required'),
            (False, True, 'completed', 'recovery-required'),
            (True, True, 'archiving', 'archiving'),
            (False, False, 'archiving', 'interrupted'),
            (False, False, 'recovery-failed', 'interrupted'),
            (False, False, 'completed', 'completed'),
            (False, False, 'failed', 'failed'),
        ):
            with self.subTest(running=running, record=record, phase=phase), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                saved = {'phase': phase, 'lastsuccess': 'previous success', 'error': 'previous error'}
                atomic_json(state / 'status.json', saved)
                before = (state / 'status.json').read_bytes()
                if record:
                    # Even an unreadable record must remain visible to the administrator.
                    (state / 'recovery.json').write_text('invalid JSON')
                output = io.StringIO()
                with (
                    patch.object(commands, 'STATE', state),
                    patch.object(commands, 'request', side_effect=BackupError('service unavailable')),
                    patch.object(commands, 'active', return_value=running),
                    patch.object(commands.os, 'geteuid', return_value=0),
                    patch.object(commands.sys, 'argv', ['omv-protondrive', 'status']),
                    contextlib.redirect_stdout(output),
                ):
                    commands.main()
                value = object_value(decode(output.getvalue()))
                self.assertEqual(value['phase'], expected)
                self.assertEqual(value['recoverypending'], record and not running)
                self.assertEqual(value['lastsuccess'], saved['lastsuccess'])
                self.assertEqual(value['authstate'], 'unavailable')
                self.assertEqual((state / 'status.json').read_bytes(), before)
                if record:
                    self.assertEqual((state / 'recovery.json').read_text(), 'invalid JSON')

    def test_recovery_entry_point_respects_the_runner_lock_and_propagates_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(commands, 'STATE', Path(directory)):
            with locked(Path(directory) / 'run.lock'), patch.object(commands, 'Recovery') as recovery:
                with self.assertRaisesRegex(BackupError, 'Another backup or recovery is running'):
                    commands.recover()
                recovery.assert_not_called()
            with (
                patch.object(commands, 'Recovery', side_effect=BackupError('recovery incomplete')),
                self.assertRaisesRegex(BackupError, 'recovery incomplete'),
            ):
                commands.recover()

    def test_status_commands_pass_account_details_to_the_ui(self):
        auth = {'state': 'signed-in', 'url': '', 'error': '', 'email': 'test@example.org', 'organization': 'Test team'}
        for command in ('status', 'auth-status'):
            output = io.StringIO()
            with (
                self.subTest(command=command),
                tempfile.TemporaryDirectory() as directory,
                patch.object(commands, 'STATE', Path(directory)),
                patch.object(commands, 'request', return_value=auth),
                patch.object(commands, 'active', return_value=False),
                patch.object(commands.os, 'geteuid', return_value=0),
                patch.object(commands.sys, 'argv', ['omv-protondrive', command]),
                contextlib.redirect_stdout(output),
            ):
                commands.main()
            value = object_value(decode(output.getvalue()))
            self.assertEqual(value['accountemail'], 'test@example.org')
            self.assertEqual(value['accountorganization'], 'Test team')

    def client(self, delay: float) -> ProtonCli:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        binary = Path(temp.name) / 'fake_proton_auth.py'
        shutil.copyfile(Path(__file__).parents[1] / 'fixtures/fake_proton_auth.py', binary)
        binary.with_suffix('.json').write_text(json.dumps({'delay': delay}))
        binary.chmod(0o755)
        config, _ = configuration()
        cli = ProtonCli(config, binary)
        self.addCleanup(cli.cancel_auth)
        return cli

    def wait_for(self, predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail('Authentication state did not converge')

    def test_login_link_is_transient_and_session_is_probed(self):
        cli = self.client(0.2)
        cli.start_auth()
        self.wait_for(lambda: bool(cli.status()['url']))
        self.assertEqual(cli.start_auth()['url'], cli.status()['url'])
        self.wait_for(lambda: cli.status()['state'] == 'signed-in')
        self.assertEqual(cli.status()['url'], '')
        self.assertEqual(cli.status()['email'], 'test@example.org')
        self.assertEqual(cli.status()['organization'], 'Test team')

    def test_identity_is_cleared_on_logout_and_new_login(self):
        cli = self.client(10)
        cli.probe()
        self.assertTrue(cli.status()['email'])
        cli.logout()
        self.assertEqual(cli.status()['state'], 'signed-out')
        self.assertEqual(cli.status()['email'], '')
        self.assertEqual(cli.status()['organization'], '')
        cli.probe()
        cli.start_auth()
        self.assertEqual(cli.status()['email'], '')
        self.assertEqual(cli.status()['organization'], '')

    def test_cancel_reaps_login_and_clears_url(self):
        cli = self.client(10)
        cli.start_auth()
        self.wait_for(lambda: bool(cli.status()['url']))
        proc = cli.login
        assert proc is not None
        cli.cancel_auth()
        self.assertIsNotNone(proc.poll())
        self.assertEqual(cli.status()['url'], '')
        self.assertIsNone(cli.login)
