import unittest
from unittest.mock import patch

from helpers import configuration

from protondrive.common import BackupError
from protondrive.protoncli import ProtonCli, entries, node_result, parse_json


class ProtonTests(unittest.TestCase):
    def test_probe_exposes_only_owner_display_fields(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        root = {
            'uid': 'private-root-id',
            'ownedBy': {'email': ' user@example.org ', 'organization': 'Example', 'extra': 'private'},
        }
        with patch.object(cli, 'info', return_value=root):
            self.assertEqual(
                cli.probe(),
                {'state': 'signed-in', 'url': '', 'error': '', 'email': 'user@example.org', 'organization': 'Example'},
            )

    def test_missing_or_malformed_owner_clears_previous_identity(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        for owner in (None, [], {}, {'email': 123, 'organization': {'name': 'unexpected'}}):
            with self.subTest(owner=owner), patch.object(cli, 'info', return_value={'uid': 'root', 'ownedBy': owner}):
                cli.auth = {'state': 'signed-in', 'email': 'previous@example.org', 'organization': 'Previous'}
                status = cli.probe()
                self.assertEqual(status['state'], 'signed-in')
                self.assertEqual(status['email'], '')
                self.assertEqual(status['organization'], '')

    def test_finished_probe_cannot_restore_identity_after_auth_state_changes(self):
        config, _ = configuration()
        cli = ProtonCli(config)

        def changed_session(path):
            cli.auth = {'state': 'signed-out', 'url': '', 'error': ''}
            return {'uid': 'root', 'ownedBy': {'email': 'previous@example.org'}}

        with patch.object(cli, 'info', side_effect=changed_session):
            status = cli.probe()
        self.assertEqual(status['state'], 'signed-out')
        self.assertEqual(status['email'], '')

    def test_multiline_json_with_leading_diagnostic(self):
        self.assertEqual(parse_json('info: [diagnostic]\n[\n{"uid":"one","ok":true}\n]'), [{'uid': 'one', 'ok': True}])

    def test_unknown_output_is_not_empty_success(self):
        for value in ('', 'all good', '[{"ok":true}] trailing junk'):
            with self.subTest(value=value), self.assertRaises(BackupError):
                parse_json(value)
        with self.assertRaises(BackupError):
            entries({'unknown': []})

    def test_wrapped_name_and_plaintext_size(self):
        data = [
            {
                'name': {'ok': True, 'value': 'a.tar.zst'},
                'uid': 'a',
                'type': 'file',
                'activeRevision': {'ok': True, 'value': {'claimedSize': 12, 'storageSize': 500}},
            }
        ]
        self.assertEqual(entries(data)[0]['size'], 12)

    def test_non_success_node_result_fails_even_with_exit_zero(self):
        for data in ([], [{'ok': False, 'uid': 'a'}], [{'ok': True, 'uid': 'other'}]):
            with self.subTest(data=data), self.assertRaises(BackupError):
                node_result(data, 'a')

    def test_paths_and_unknown_types_fail_closed(self):
        for name, kind in (('../other', 'file'), ('folder/file', 'file'), ('a', 'unknown')):
            with self.assertRaises(BackupError):
                entries([{'name': name, 'type': kind, 'uid': 'a'}])

    def test_purge_does_not_delete_mismatched_trash_uid(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        entry = {'name': 'backup', 'type': 'file', 'uid': 'a', 'size': 10}
        with (
            patch.object(cli, 'list', return_value=[entry]),
            patch.object(cli, 'info', return_value={'uid': 'other', 'type': 'file'}),
            patch.object(cli, '_run', return_value=[{'uid': 'a', 'ok': True}]) as run,
        ):
            with self.assertRaises(BackupError):
                cli.purge('/my-files/backups', entry)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][1], 'trash')

    def test_duplicate_name_prevents_trash(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        entry = {'name': 'backup', 'type': 'file', 'uid': 'a', 'size': 10}
        with (
            patch.object(cli, 'list', return_value=[entry, {**entry, 'type': 'folder'}]),
            patch.object(cli, '_run') as run,
        ):
            with self.assertRaises(BackupError):
                cli.purge('/my-files/backups', entry)
            run.assert_not_called()

    def test_cli_arguments_are_not_shell_commands(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        with patch.object(cli, '_run') as run:
            cli.upload('/tmp/a ; touch evil', '/my-files/backups')
            args = run.call_args.args[0]
            self.assertIn('/tmp/a ; touch evil', args)
            self.assertIn('skip', args)
