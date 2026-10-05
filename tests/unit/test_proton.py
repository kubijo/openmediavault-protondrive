import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from helpers import configuration
from protondrive.common import BackupError
from protondrive.daemon import load_owner_id
from protondrive.json_data import JSONValue, validate
from protondrive.models import RemoteEntry
from protondrive.protoncli import OWNER_MARKER, ProtonCli, entries, node_result, parse_json


class ProtonTests(unittest.TestCase):
    def test_transfer_status_tracks_each_command_and_clears_on_failure(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        folder = config['remotepath'] + '/set'
        now = [10.0]
        for operation, name, phase in (
            ('upload', 'backup.tar.zst', 'Archive upload'),
            ('download', 'backup.tar.zst', 'Verification download'),
            ('upload', 'backup.tar.zst.manifest.json', 'Manifest upload'),
            ('download', 'backup.tar.zst.manifest.json', 'Manifest download'),
            ('upload', OWNER_MARKER, 'Ownership marker upload'),
            ('download', OWNER_MARKER, 'Ownership marker download'),
        ):
            for failure in (False, True):
                with self.subTest(operation=operation, name=name, failure=failure):

                    def communicate(timeout: float, phase: str = phase, name: str = name) -> tuple[str, str]:
                        now[0] += 3
                        self.assertEqual(
                            cli.transfer_status(),
                            {
                                'transferphase': phase,
                                'transferfile': name,
                                'transferelapsed': 3,
                            },
                        )
                        return '', ''

                    process = Mock(returncode=1 if failure else 0, communicate=communicate)
                    with (
                        patch('protondrive.protoncli.subprocess.Popen', return_value=process),
                        patch('protondrive.protoncli.time.monotonic', side_effect=lambda: now[0]),
                    ):
                        args = (
                            (Path('/staging') / name, folder)
                            if operation == 'upload'
                            else (folder + '/' + name, '/scratch')
                        )
                        if failure:
                            with self.assertRaises(BackupError):
                                getattr(cli, operation)(*args)
                        else:
                            getattr(cli, operation)(*args)
                    self.assertEqual(
                        cli.transfer_status(), {'transferphase': '', 'transferfile': '', 'transferelapsed': 0}
                    )

    def test_transfer_timeout_clears_activity_and_kills_process_group(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        process = Mock(pid=123, communicate=Mock(side_effect=[subprocess.TimeoutExpired('proton', 1), ('', '')]))
        with (
            patch('protondrive.protoncli.subprocess.Popen', return_value=process),
            patch('protondrive.protoncli.os.killpg') as kill,
            self.assertRaisesRegex(BackupError, 'timed out'),
        ):
            cli.download(config['remotepath'] + '/set/archive.tar.zst', '/scratch')
        kill.assert_called_once()
        self.assertEqual(cli.transfer_status()['transferphase'], '')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.claims_directory = Path(temporary.name) / 'pending-claims'

    def test_local_owner_id_is_private_and_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'proton/owner-id'
            owner = load_owner_id(path)
            self.assertEqual(load_owner_id(path), owner)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('invalid\n')
            with self.assertRaisesRegex(BackupError, 'Invalid local Proton backup owner identity'):
                load_owner_id(path)

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
        owners: tuple[object, ...] = (None, [], {}, {'email': 123, 'organization': {'name': 'unexpected'}})
        for owner in owners:
            with self.subTest(owner=owner), patch.object(cli, 'info', return_value={'uid': 'root', 'ownedBy': owner}):
                cli.auth = {'state': 'signed-in', 'email': 'previous@example.org', 'organization': 'Previous'}
                status = cli.probe()
                self.assertEqual(status['state'], 'signed-in')
                self.assertEqual(status['email'], '')
                self.assertEqual(status['organization'], '')

    def test_finished_probe_cannot_restore_identity_after_auth_state_changes(self):
        config, _ = configuration()
        cli = ProtonCli(config)

        def changed_session(path: str) -> dict[str, JSONValue]:
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
        self.assertEqual(entries(validate(data))[0]['size'], 12)

    def test_plain_revision_from_live_cli_listing(self):
        data = [
            {
                'name': 'system-20261004T0842Z.tar.zst',
                'uid': 'archive-id',
                'type': 'file',
                'activeRevision': {'claimedSize': 278, 'storageSize': 512},
            }
        ]
        self.assertEqual(entries(validate(data))[0]['size'], 278)

    def test_non_success_node_result_fails_even_with_exit_zero(self):
        for data in ([], [{'ok': False, 'uid': 'a'}], [{'ok': True, 'uid': 'other'}]):
            with self.subTest(data=data), self.assertRaises(BackupError):
                node_result(validate(data), 'a')

    def test_paths_and_unknown_types_fail_closed(self):
        for name, kind in (('../other', 'file'), ('folder/file', 'file'), ('a', 'unknown')):
            with self.assertRaises(BackupError):
                entries([{'name': name, 'type': kind, 'uid': 'a'}])

    def test_trash_only_uses_the_managed_folder_and_checks_uid(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        entry: RemoteEntry = {'name': 'backup', 'type': 'file', 'uid': 'a', 'size': 10}
        folder = config['remotepath'] + '/set'
        with (
            patch.object(cli, 'list', return_value=[entry]),
            patch.object(cli, '_run', return_value=[{'uid': 'a', 'ok': True}]) as run,
        ):
            cli.trash(folder, entry)
            run.assert_called_once_with(['filesystem', 'trash', folder + '/backup'])
            run.return_value = [{'uid': 'other', 'ok': True}]
            with self.assertRaises(BackupError):
                cli.trash(folder, entry)

    def test_duplicate_name_prevents_trash(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        entry: RemoteEntry = {'name': 'backup', 'type': 'file', 'uid': 'a', 'size': 10}
        with (
            patch.object(cli, 'list', return_value=[entry, {**entry, 'type': 'folder'}]),
            patch.object(cli, '_run') as run,
        ):
            with self.assertRaises(BackupError):
                cli.trash(config['remotepath'] + '/set', entry)
            run.assert_not_called()

    def test_storage_commands_cannot_escape_the_managed_root(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        root = config['remotepath']
        cli.check_storage(['filesystem', 'info', '/my-files'])
        cli.check_storage(['filesystem', 'list', root + '/set'])
        cli.check_storage(['filesystem', 'create-folder', '/my-files', root.rsplit('/', 1)[1]])
        for command in (
            ['filesystem', 'list', '/my-files/other'],
            ['filesystem', 'create-folder', '/my-files', 'other'],
            ['filesystem', 'upload', '-f', 'skip', '-d', 'skip', '-t', '/tmp/archive', '/my-files/other'],
            ['filesystem', 'download', '-f', 'skip', '-d', 'skip', '/my-files/other/file', '/tmp'],
            ['filesystem', 'trash', '/my-files/other/file'],
            ['filesystem', 'trash', root + '/../other/file'],
            ['filesystem', 'delete', '/trash/file'],
        ):
            with self.subTest(command=command), self.assertRaises(BackupError):
                cli.check_storage(command)

    def test_cli_arguments_are_not_shell_commands(self):
        config, _ = configuration()
        cli = ProtonCli(config)
        with patch.object(cli, '_run') as run:
            cli.upload('/tmp/a ; touch evil', config['remotepath'] + '/set')
            assert run.call_args is not None
            args = validate(cast(object, run.call_args.args[0]))
            assert isinstance(args, list)
            self.assertIn('/tmp/a ; touch evil', args)
            self.assertIn('skip', args)

    def test_empty_instance_gets_a_private_owner_marker_and_reuses_it(self):
        config, _ = configuration()
        cli = ProtonCli(config, owner_id='local-owner', claims_directory=self.claims_directory)
        folder = config['remotepath'] + '/' + config['instanceuuid']
        marker: RemoteEntry = {'name': OWNER_MARKER, 'type': 'file', 'uid': 'marker-id', 'size': None}
        remote: dict[str, bytes] = {}

        def listing(path: str) -> list[RemoteEntry]:
            self.assertEqual(path, folder)
            return [marker] if remote else []

        def upload(path: str | Path, destination: str) -> None:
            self.assertEqual(destination, folder)
            self.assertNotIn(OWNER_MARKER, remote)
            remote[OWNER_MARKER] = Path(path).read_bytes()

        def download(path: str, destination: str | Path) -> None:
            self.assertEqual(path, folder + '/' + OWNER_MARKER)
            (Path(destination) / OWNER_MARKER).write_bytes(remote[OWNER_MARKER])

        with (
            patch.object(cli, 'ensure_folder') as ensure,
            patch.object(cli, 'list', side_effect=listing),
            patch.object(cli, 'upload', side_effect=upload) as uploader,
            patch.object(cli, 'download', side_effect=download),
        ):
            cli.ensure_instance_owned()
            cli.ensure_instance_owned()
        ensure.assert_any_call(folder)
        uploader.assert_called_once()
        self.assertEqual(list(self.claims_directory.iterdir()), [])
        self.assertEqual(
            json.loads(remote[OWNER_MARKER]),
            {'format': 1, 'instanceuuid': config['instanceuuid'], 'ownerid': 'local-owner'},
        )

    def test_unmarked_existing_instance_is_not_claimed(self):
        config, _ = configuration()
        cli = ProtonCli(config, owner_id='local-owner', claims_directory=self.claims_directory)
        with (
            patch.object(cli, 'ensure_folder'),
            patch.object(cli, 'list', return_value=[{'name': 'existing-set', 'type': 'folder', 'uid': 'set-id'}]),
            patch.object(cli, 'upload') as upload,
            self.assertRaisesRegex(BackupError, 'no owner marker'),
        ):
            cli.ensure_instance_owned()
        upload.assert_not_called()

    def test_claim_rejects_data_arriving_while_marker_is_uploaded(self):
        config, _ = configuration()
        cli = ProtonCli(config, owner_id='local-owner', claims_directory=self.claims_directory)
        marker: RemoteEntry = {'name': OWNER_MARKER, 'type': 'file', 'uid': 'marker-id', 'size': None}
        foreign = {'name': 'foreign-set', 'type': 'folder', 'uid': 'foreign-id'}
        with (
            patch.object(cli, 'ensure_folder'),
            patch.object(cli, 'list', side_effect=[[], [marker, foreign]]),
            patch.object(cli, 'upload') as upload,
            patch.object(cli, 'download') as download,
            self.assertRaisesRegex(BackupError, 'changed during ownership claim'),
        ):
            cli.ensure_instance_owned()
        upload.assert_called_once()
        download.assert_not_called()
        pending = list(self.claims_directory.iterdir())
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(pending[0].read_text())['ownerid'], 'local-owner')
        # Restarting the service must not trust the marker left by the rejected claim.
        restarted = ProtonCli(config, owner_id='local-owner', claims_directory=self.claims_directory)
        for attempt in (cli, restarted):
            with (
                patch.object(attempt, 'ensure_folder') as ensure,
                self.assertRaisesRegex(BackupError, 'claim is incomplete'),
            ):
                attempt.ensure_instance_owned()
            ensure.assert_not_called()

    def test_interrupted_claim_stays_blocked_after_restart(self):
        config, _ = configuration()
        cli = ProtonCli(config, owner_id='local-owner', claims_directory=self.claims_directory)
        with (
            patch.object(cli, 'ensure_folder'),
            patch.object(cli, 'list', return_value=[]),
            patch.object(cli, 'upload', side_effect=BackupError('Connection lost')),
            self.assertRaisesRegex(BackupError, 'Connection lost'),
        ):
            cli.ensure_instance_owned()
        restarted = ProtonCli(config, owner_id='local-owner', claims_directory=self.claims_directory)
        with (
            patch.object(restarted, 'ensure_folder') as ensure,
            self.assertRaisesRegex(BackupError, 'claim is incomplete'),
        ):
            restarted.ensure_instance_owned()
        ensure.assert_not_called()

    def test_foreign_or_ambiguous_instance_owner_is_rejected(self):
        config, _ = configuration()
        cli = ProtonCli(config, owner_id='local-owner')
        marker: RemoteEntry = {'name': OWNER_MARKER, 'type': 'file', 'uid': 'marker-id', 'size': None}

        def foreign_marker(path: str, destination: str | Path) -> None:
            value = {'format': 1, 'instanceuuid': config['instanceuuid'], 'ownerid': 'foreign-owner'}
            (Path(destination) / OWNER_MARKER).write_text(json.dumps(value))

        with (
            patch.object(cli, 'ensure_folder'),
            patch.object(cli, 'list', return_value=[marker]),
            patch.object(cli, 'download', side_effect=foreign_marker),
            patch.object(cli, 'upload') as upload,
            self.assertRaisesRegex(BackupError, 'different installation'),
        ):
            cli.ensure_instance_owned()
        upload.assert_not_called()
        with (
            patch.object(cli, 'ensure_folder'),
            patch.object(cli, 'list', return_value=[marker, {**marker, 'uid': 'second'}]),
            patch.object(cli, 'download') as download,
            self.assertRaisesRegex(BackupError, 'ambiguous'),
        ):
            cli.ensure_instance_owned()
        download.assert_not_called()
