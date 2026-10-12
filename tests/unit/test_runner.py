import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import configuration
from protondrive import runner
from protondrive.common import BackupError
from protondrive.completion import read_completion
from protondrive.compose_source import ComposeInventory, ComposeJob
from protondrive.json_data import decode, object_value
from protondrive.models import BackupSet, Configuration, Destination
from protondrive.operation import Cancelled


class RunnerTests(unittest.TestCase):
    def test_pending_retries_only_unconfirmed_destinations(self):
        config, item = configuration()
        secondary = Destination(id='secondary', kind='future', name='Secondary', enable=True, root='/backups')
        config['destinations'].append(secondary)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / 'appData-20261010T1200Z.tar.zst'
            archive.write_bytes(b'archive')
            archive.with_name(archive.name + '.manifest.json').write_text(json.dumps({'sha256': 'a' * 64}))
            calls: list[str] = []

            def transfer(config: Configuration, item: BackupSet, path: Path, destination: Destination) -> None:
                calls.append(destination['id'])
                runner.atomic_json(
                    runner.receipt(path, destination),
                    {'folder': runner.remote_folder(config, item, destination), 'sha256': 'a' * 64},
                )

            with patch.object(runner, 'upload', side_effect=transfer):
                runner.pending(config, item, directory)
                runner.pending(config, item, directory)
            self.assertEqual(calls, ['protondrive', 'secondary'])

    def test_failed_target_does_not_prevent_other_upload_and_is_retried(self):
        config, item = configuration()
        config['destinations'].append(
            Destination(id='secondary', kind='future', name='Secondary', enable=True, root='/backups')
        )
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'appData-20261010T1200Z.tar.zst'
            archive.write_bytes(b'archive')
            archive.with_name(archive.name + '.manifest.json').write_text(json.dumps({'sha256': 'a' * 64}))
            calls: list[str] = []

            def transfer(config: Configuration, item: BackupSet, path: Path, destination: Destination) -> None:
                calls.append(destination['id'])
                if destination['id'] == 'protondrive' and calls.count('protondrive') == 1:
                    raise BackupError('Target unavailable', code='unavailable')
                runner.atomic_json(
                    runner.receipt(path, destination),
                    {'folder': runner.remote_folder(config, item, destination), 'sha256': 'a' * 64},
                )

            with patch.object(runner, 'upload', side_effect=transfer):
                with self.assertRaisesRegex(BackupError, 'Target unavailable'):
                    runner.upload_all(config, item, archive)
                self.assertEqual(calls, ['protondrive', 'secondary'])
                runner.upload_all(config, item, archive)
            self.assertEqual(calls, ['protondrive', 'secondary', 'protondrive'])

    def test_cancellation_does_not_start_another_destination(self):
        config, item = configuration()
        config['destinations'].append(
            Destination(id='secondary', kind='future', name='Secondary', enable=True, root='/backups')
        )
        calls = Mock(side_effect=Cancelled('cancelled'))
        with (
            patch.object(runner, 'confirmed', return_value=False),
            patch.object(runner, 'upload', calls),
            self.assertRaises(Cancelled),
        ):
            runner.upload_all(config, item, Path('/unused/archive.tar.zst'))
        calls.assert_called_once()

    def test_local_pruning_waits_for_every_enabled_destination(self):
        config, item = configuration()
        item['localkeep'] = 1
        secondary = Destination(id='secondary', kind='future', name='Secondary', enable=True, root='/backups')
        config['destinations'].append(secondary)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archives = [directory / f'appData-20261010T12{minute:02}Z.tar.zst' for minute in (0, 1)]
            for archive in archives:
                archive.write_bytes(b'archive')
                archive.with_name(archive.name + '.manifest.json').write_text(json.dumps({'sha256': 'a' * 64}))
                for destination in config['destinations']:
                    runner.atomic_json(
                        runner.receipt(archive, destination),
                        {'folder': runner.remote_folder(config, item, destination), 'sha256': 'a' * 64},
                    )
            runner.receipt(archives[1], secondary).unlink()
            runner.prune_local(config, item, directory)
            self.assertTrue(all(path.exists() for path in archives))
            runner.atomic_json(
                runner.receipt(archives[1], secondary),
                {'folder': runner.remote_folder(config, item, secondary), 'sha256': 'a' * 64},
            )
            runner.prune_local(config, item, directory)
            self.assertFalse(archives[0].exists())
            self.assertTrue(archives[1].exists())

    def test_phase_changes_clear_stale_item_but_keep_run_details(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(runner, 'STATE', Path(temporary)):
            runner.status(
                'uploading',
                message='archive.tar.zst',
                error='Previous failure',
                errorcode='busy',
                lastsuccess='previous',
            )
            runner.status('retention')
            value = object_value(decode((Path(temporary) / 'status.json').read_text()))
            self.assertEqual(value['message'], '')
            self.assertEqual(value['error'], '')
            self.assertEqual(value['errorcode'], '')
            self.assertEqual(value['lastsuccess'], 'previous')

    def exercise(
        self, fail: bool = False, unexpected: bool = False, compose: bool = False, failed_verification: bool = False
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            config, _ = configuration()
            config['stagingpath'] = str(root / 'staging')
            events: list[str] = []
            recovery = Mock(restore=lambda: events.append('restore'))
            source = root / 'copies' / 'database'
            if failed_verification:
                source.parent.mkdir(parents=True)
                (source.parent / 'dump.sql.gz').write_bytes(b'database dump')
                config['sets'][0]['paths'] = str(source.parent)
                config['sets'][0]['requiredbackups'] = [
                    {
                        'name': 'database',
                        'directory': str(source.parent),
                        'pattern': '*.sql.gz',
                        'max_age_hours': 36,
                    }
                ]
            if compose:
                job_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
                config['sets'][0]['composesources'] = [{'job_id': job_id, 'stacks': ['database']}]
                source.mkdir(parents=True)
                (source / 'dump.sql').write_text('sealed dump')
                available = ComposeInventory(
                    source.parent,
                    (ComposeJob(job_id, 'Database', False, True, False, False, False, ''),),
                    ('database',),
                    0,
                )
                stack.enter_context(patch.object(runner, 'compose_inventory', return_value=available))

                def capture_compose(*_args: object) -> tuple[Path, ...]:
                    events.append('capture')
                    return (source,)

                stack.enter_context(patch.object(runner, 'capture_compose', side_effect=capture_compose))

            def archive(_item: BackupSet, path: Path, reserve: int, extra_sources: tuple[Path, ...]) -> None:
                events.append('archive')
                if compose:
                    self.assertEqual(len(extra_sources), 1)
                    self.assertEqual((extra_sources[0] / 'dump.sql').read_text(), 'sealed dump')
                    self.assertNotEqual(extra_sources[0], source)
                path.write_bytes(b'partial')
                if fail:
                    if unexpected:
                        raise RuntimeError('private archive diagnostic')
                    raise BackupError('injected tar failure')

            def publish(item: BackupSet, path: Path, *args: object, **kwargs: object) -> Path:
                events.append('publish')
                final = path.with_suffix('')
                path.rename(final)
                return final

            def upload(*args: object) -> None:
                events.append('upload')

            replacements: list[tuple[str, object]] = [
                ('STATE', root),
                ('load', lambda: config),
                ('Recovery', lambda: recovery),
                ('estimate', Mock(return_value=1)),
                ('check_space', Mock()),
                ('archive', archive),
                ('publish', publish),
                ('prune_local', Mock()),
                ('request', Mock(return_value=[])),
                ('upload', Mock(side_effect=upload)),
            ]
            if failed_verification:
                replacements.append(
                    ('verify_archived_backup', Mock(side_effect=BackupError('archived backup mismatch')))
                )
            for name, value in replacements:
                stack.enter_context(patch.object(runner, name, value))
            stack.enter_context(patch('signal.signal'))
            stack.enter_context(patch('grp.getgrnam', return_value=SimpleNamespace(gr_gid=0)))
            stack.enter_context(patch('os.chown'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if fail or failed_verification:
                with self.assertRaisesRegex(RuntimeError if unexpected else BackupError, 'private|injected|mismatch'):
                    runner.run()
                status = object_value(decode((root / 'status.json').read_text()))
                if unexpected:
                    self.assertEqual(status['errorcode'], 'internal')
                    self.assertRegex(str(status['error']), r'reference [0-9a-f]{12}')
                    self.assertNotIn('private archive diagnostic', str(status['error']))
                self.assertNotIn('upload', events)
                self.assertFalse(list((root / 'staging').rglob('*.partial')))
                self.assertEqual(events[-1], 'restore')
                self.assertIsNone(read_completion(root))
            else:
                runner.run()
                self.assertEqual(events, ['restore', *(['capture'] if compose else []), 'archive', 'publish', 'upload'])
                if compose:
                    self.assertFalse(list((root / 'staging').glob('.compose-capture-*')))
                completion = read_completion(root)
                assert completion is not None
                self.assertEqual(completion.generation, 1)
                status = object_value(decode((root / 'status.json').read_text()))
                self.assertEqual(completion.timestamp, status['lastsuccess'])
                self.assertEqual(status['capture'], 'unverified' if compose else 'live')

    def test_archive_and_upload_after_legacy_recovery_check(self):
        self.exercise()

    def test_compose_output_is_sealed_before_archiving(self):
        self.exercise(compose=True)

    def test_archive_failure_never_uploads(self):
        self.exercise(fail=True)

    def test_required_backup_mismatch_never_uploads(self):
        self.exercise(failed_verification=True)

    def test_unexpected_archive_failure_has_a_redacted_reference(self):
        self.exercise(fail=True, unexpected=True)
