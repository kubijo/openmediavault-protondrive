"""Crash recovery assertions fail closed and retain evidence on incomplete recovery."""

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO
from unittest.mock import patch

from tests.integration import live_crash_guest as crash
from tests.integration import live_ui_guest as flow

from helpers import configuration
from protondrive.archive import digest
from protondrive.common import atomic_json


class CrashTests(unittest.TestCase):
    def test_reboot_is_refused_before_checkpoint_mutation_when_not_armed(self) -> None:
        with (
            patch.object(crash, 'read_record', return_value=self.record()),
            patch.object(crash, 'checkpoint') as checkpoint,
        ):
            with self.assertRaisesRegex(RuntimeError, 'not armed'):
                crash.reboot_ready()
            checkpoint.assert_not_called()

    def record(self) -> crash.CrashRecord:
        return {
            'token': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            'boot_id': 'old',
            'mode': 'kill',
            'preserved': {},
            'fixtures': {},
        }

    def test_gate_restoration_refuses_foreign_content_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(flow, 'FLOW', Path(temporary)):
            crash.save(self.record())
            crash.restore_gate()
            self.assertEqual(crash.gate().read_text(), self.record()['token'])
            crash.gate().write_text('foreign')
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                crash.restore_gate()
            crash.gate().unlink()
            crash.gate().symlink_to(Path(temporary) / 'absent')
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                crash.restore_gate()

    def test_changed_pending_archive_is_preserved_for_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(flow, 'FLOW', Path(temporary)):
            payload = Path(temporary) / 'archive'
            payload.write_bytes(b'original')
            value = self.record()
            value['fixtures'][str(payload)] = digest(payload)
            crash.save(value)
            payload.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'changed retained'):
                crash.remove_fixtures()
            self.assertTrue(payload.exists())
            self.assertTrue(crash.read_record()['fixtures'])
            payload.write_bytes(b'original')
            crash.remove_fixtures()
            self.assertFalse(payload.exists())
            self.assertEqual(crash.read_record()['fixtures'], {})

    def test_failed_recovery_requires_record_and_pending_archive_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pending = directory / 'system-19700101T0000Z.tar.zst'
            pending.write_bytes(b'complete')
            value = self.record()
            value['fixtures'][str(pending)] = digest(pending)
            atomic_json(directory / 'recovery.json', {'ids': ['running']})
            with (
                patch.object(crash, 'STATE', directory),
                patch.object(crash, 'read_record', return_value=value),
                patch.object(crash, 'wait_stopped'),
                patch.object(flow, 'record', return_value={'containers': ['running', 'stopped']}),
                patch.object(flow, 'run', return_value=SimpleNamespace(stdout=b'exit-code')),
                patch.object(flow, 'running', return_value=False),
            ):
                self.assertTrue(crash.failed()['recovery_record_retained'])
                crash.receipt(pending).touch()
                with self.assertRaisesRegex(RuntimeError, 'unexpectedly confirmed'):
                    crash.failed()
                crash.receipt(pending).unlink()
                atomic_json(directory / 'recovery.json', {'ids': []})
                with self.assertRaisesRegex(RuntimeError, 'lost its record'):
                    crash.failed()

    def test_cleanup_restores_gate_before_attempting_container_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'crash.json').touch()
            (directory / 'record.json').touch()
            (directory / 'recovery.json').touch()
            events: list[str] = []

            def restore() -> None:
                events.append('gate')

            def run(*args: str) -> None:
                self.assertEqual(events, ['gate'])
                raise RuntimeError('recovery failed')

            with (
                patch.object(flow, 'FLOW', directory),
                patch.object(flow, 'STATE', directory),
                patch.object(flow, 'active', return_value=False),
                patch.object(flow, 'crash_module', return_value=SimpleNamespace(restore_gate=restore)),
                patch.object(flow, 'run', side_effect=run),
                self.assertRaisesRegex(RuntimeError, 'recovery failed'),
            ):
                flow.cleanup()
            self.assertTrue((directory / 'crash.json').exists())


class CrashCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        config, app_data = configuration()
        system = app_data.copy()
        system.update(name='system', uuid='cccccccc-cccc-4ccc-8ccc-cccccccccccc')
        config['sets'] = [app_data, system]
        config['stagingpath'] = str(self.root / 'staging')
        self.partials: list[Path] = []
        for item in config['sets']:
            directory = Path(config['stagingpath']) / item['uuid']
            directory.mkdir(parents=True)
            partial = directory / f'{item["name"]}-20261005T1201Z.tar.zst.partial'
            partial.touch()
            self.partials.append(partial)
        self.directory = Path(config['stagingpath']) / system['uuid']
        self.source = self.directory / 'system-20261005T1200Z.tar.zst'
        self.source.write_bytes(b'complete archive bytes')
        crash.receipt(self.source).touch()
        atomic_json(
            self.source.with_name(self.source.name + '.manifest.json'),
            {
                'format': 1,
                'instanceuuid': config['instanceuuid'],
                'setuuid': system['uuid'],
                'archive': self.source.name,
                'timestamp': '20261005T1200Z',
                'size': self.source.stat().st_size,
                'sha256': digest(self.source),
            },
        )
        self.stack.enter_context(patch.object(flow, 'FLOW', self.root))
        self.stack.enter_context(patch.object(flow, 'held', return_value={'held': True}))
        self.stack.enter_context(patch.object(flow, 'run'))
        self.stack.enter_context(patch.object(crash, 'load', return_value=config))
        self.stack.enter_context(patch.object(crash.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=os.getgid())))
        self.stack.enter_context(patch.object(crash.os, 'fchown'))
        self.preserved = crash.hashes()
        crash.save(
            {
                'token': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                'boot_id': 'old',
                'mode': 'kill',
                'preserved': self.preserved,
                'fixtures': {},
            }
        )
        self.pending = self.directory / 'system-19700101T0000Z.tar.zst'
        self.manifest = self.pending.with_name(self.pending.name + '.manifest.json')

    def assert_cleaned(self) -> None:
        crash.remove_fixtures()
        crash.remove_fixtures()
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(list(self.directory.glob('.crash-*')))
        self.assertTrue(all(not partial.exists() for partial in self.partials))
        self.assertEqual(crash.read_record()['fixtures'], {})
        self.assertNotIn('workspace', crash.read_record())
        crash.verify_hashes(self.preserved)

    def test_checkpoint_tracks_both_sets_with_appdata_first(self) -> None:
        value = crash.checkpoint()
        self.assertTrue(all(str(partial) in value['fixtures'] for partial in self.partials))
        crash.verify_hashes(value['fixtures'])
        self.assertEqual(self.pending.read_bytes(), self.source.read_bytes())
        self.assert_cleaned()

    def test_interrupted_copy_leaves_only_owned_workspace_to_remove(self) -> None:
        def interrupted_copy(source: BinaryIO, destination: BinaryIO) -> None:
            destination.write(source.read(4))
            raise InterruptedError('copy interrupted')

        with (
            patch.object(crash.shutil, 'copyfileobj', side_effect=interrupted_copy),
            self.assertRaises(InterruptedError),
        ):
            crash.checkpoint()
        self.assertFalse(self.pending.exists())
        self.assert_cleaned()

    def test_interrupted_manifest_write_is_cleanable_before_publication(self) -> None:
        def interrupted_write(
            path: str | Path,
            value: object,
            mode: int = 0o600,
            *,
            owner: tuple[int, int] | None = None,
        ) -> None:
            atomic_json(path, value, mode, owner=owner)
            if Path(path).name == self.manifest.name:
                raise InterruptedError('manifest written but not journaled')

        with patch.object(crash, 'atomic_json', side_effect=interrupted_write), self.assertRaises(InterruptedError):
            crash.checkpoint()
        self.assertFalse(self.manifest.exists())
        self.assert_cleaned()

    def test_interrupted_publication_leaves_both_names_journaled(self) -> None:
        link = os.link
        for count in (1, 2):
            with self.subTest(published_files=count):
                # Recreate the two held-runner partials for the next checkpoint.
                for partial in self.partials:
                    partial.touch()
                calls = 0

                def interrupted_link(source: Path, destination: Path, limit: int = count) -> None:
                    nonlocal calls
                    link(source, destination)
                    calls += 1
                    if calls == limit:
                        raise InterruptedError('publication interrupted')

                with patch.object(crash.os, 'link', side_effect=interrupted_link), self.assertRaises(InterruptedError):
                    crash.checkpoint()
                self.assertIn(str(self.pending), crash.read_record()['fixtures'])
                self.assertIn(str(self.manifest), crash.read_record()['fixtures'])
                self.assert_cleaned()

    def test_interrupted_workspace_creation_is_cleanable(self) -> None:
        with (
            patch.object(Path, 'mkdir', side_effect=InterruptedError('before mkdir')),
            self.assertRaises(InterruptedError),
        ):
            crash.checkpoint()
        self.assertIn('workspace', crash.read_record())
        self.assert_cleaned()

    def test_failed_publication_journal_leaves_no_visible_pair(self) -> None:
        save = crash.save

        def interrupted_save(value: crash.CrashRecord) -> None:
            if str(self.pending) in value['fixtures']:
                raise InterruptedError('publication journal failed')
            save(value)

        with patch.object(crash, 'save', side_effect=interrupted_save), self.assertRaises(InterruptedError):
            crash.checkpoint()
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.manifest.exists())
        self.assert_cleaned()

    def test_changed_published_fixture_is_retained(self) -> None:
        crash.checkpoint()
        self.manifest.write_text('foreign content')
        with self.assertRaisesRegex(RuntimeError, 'changed retained archive'):
            crash.remove_fixtures()
        self.assertEqual(self.manifest.read_text(), 'foreign content')
        self.assertIn(str(self.manifest), crash.read_record()['fixtures'])

    def test_workspace_symlink_is_not_followed_or_removed(self) -> None:
        crash.checkpoint()
        value = crash.read_record()
        assert 'workspace' in value
        workspace = Path(value['workspace'])
        original = workspace.with_name('saved-workspace')
        workspace.rename(original)
        workspace.symlink_to(original, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'workspace changed'):
            crash.remove_fixtures()
        self.assertTrue(workspace.is_symlink())
        self.assertTrue(original.is_dir())
