import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protondrive.common import BackupError
from protondrive.compose_source import ComposeInventory, ComposeJob, capture, inventory, seal, validate_source
from protondrive.models import ComposeSource

JOB_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
SOURCE = ComposeSource(job_id=JOB_ID, stacks=['database'])


class ComposeSourceTests(unittest.TestCase):
    def test_inventory_uses_omv_configuration_and_stack_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = root / 'backup'
            command.touch()

            def rpc(_actor: str, service: str, method: str, params: dict[str, object]) -> object:
                if method == 'get':
                    return {
                        'backupbackend': 'rsync',
                        'backupsharedfolderref': 'backup-ref',
                        'sharedfolderref': 'source-ref',
                        'backupmaxsize': 2,
                    }
                if service == 'ShareMgmt':
                    return str(root / ('copies' if params['uuid'] == 'backup-ref' else 'source'))
                if method == 'getJobList':
                    return {
                        'data': [
                            {
                                'uuid': JOB_ID,
                                'comment': 'Nightly',
                                'enable': False,
                                'backup': True,
                                'update': False,
                                'prune': False,
                                'skipstartstop': False,
                                'excludefilter': '',
                            }
                        ]
                    }
                if method == 'getFileListSuggest':
                    return ['*', 'database', '../unsafe']
                raise AssertionError(method)

            with (
                patch('protondrive.compose_source.BACKUP_COMMAND', command),
                patch('protondrive.compose_source.rpc', side_effect=rpc),
            ):
                available = inventory()
            self.assertIsNotNone(available)
            assert available is not None
            self.assertEqual(available.stacks, ('database',))
            self.assertEqual(available.max_volume_size, 2 * 1024**3)
            self.assertEqual(available.source_folder, root / 'source')

    def test_seal_is_independent_of_compose_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'copies' / 'database'
            source.mkdir(parents=True)
            (source / 'dump.sql').write_text('complete')
            (source.parent / 'global.env').write_text('example=yes')
            sealed = seal((source,), root / 'sealed', include_global_env=True)
            (source / 'dump.sql').write_text('replaced')
            self.assertEqual((sealed[0] / 'dump.sql').read_text(), 'complete')
            self.assertEqual((sealed[1]).read_text(), 'example=yes')
            self.assertEqual(len(seal((source,), root / 'without-global-env')), 1)
            (source.parent / 'global.env').unlink()
            with self.assertRaisesRegex(BackupError, 'global environment'):
                seal((source,), root / 'missing-global-env', include_global_env=True)

    def test_job_must_own_only_an_unscheduled_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            job = ComposeJob(JOB_ID, 'Database', False, True, False, False, False, '')
            available = ComposeInventory(folder, (job,), ('database',), 1024**3)
            validate_source(SOURCE, available)
            for forbidden in (
                ComposeJob(JOB_ID, 'Database', True, True, False, False, False, ''),
                ComposeJob(JOB_ID, 'Database', False, True, False, False, True, ''),
                ComposeJob(JOB_ID, 'Database', False, True, True, False, False, ''),
            ):
                with self.subTest(job=forbidden), self.assertRaises(BackupError):
                    validate_source(SOURCE, ComposeInventory(folder, (forbidden,), ('database',), 1024**3))

    def test_capture_requires_command_success_and_selected_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'copies'
            folder.mkdir()
            job = ComposeJob(JOB_ID, 'Database', False, True, False, False, False, '')
            available = ComposeInventory(folder, (job,), ('database',), 1024**3)
            command = root / 'backup'
            command.write_text('#!/bin/sh\nexit 7\n')
            command.chmod(0o700)
            with (
                patch('protondrive.compose_source.BACKUP_COMMAND', command),
                self.assertRaisesRegex(BackupError, 'exit 7'),
            ):
                capture(SOURCE, available, root / 'capture.log')
            command.write_text('#!/bin/sh\nexit 0\n')
            with (
                patch('protondrive.compose_source.BACKUP_COMMAND', command),
                self.assertRaisesRegex(BackupError, 'did not produce'),
            ):
                capture(SOURCE, available, root / 'capture.log')
            (folder / 'database').mkdir()
            with patch('protondrive.compose_source.BACKUP_COMMAND', command):
                self.assertEqual(capture(SOURCE, available, root / 'capture.log'), (folder / 'database',))
