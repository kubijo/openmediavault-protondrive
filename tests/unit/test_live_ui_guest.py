"""The live UI fixture must refuse production state and preserve interrupted recovery."""

import fcntl
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.integration import live_ui_guest as guest

from helpers import configuration
from protondrive.models import Configuration


class LiveUIFixtureTests(unittest.TestCase):
    def test_lease_excludes_another_controller_and_releases_on_disconnect(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / 'lease'
            with (
                patch.object(guest, 'LOCK', lock),
                patch('sys.stdin', io.StringIO()),
                patch('sys.stdout', io.StringIO()),
            ):
                with lock.open('a') as owner:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaisesRegex(RuntimeError, 'Another live UI flow'):
                        guest.lease()
                self.assertEqual(guest.lease(), {'released': True})
                self.assertEqual(guest.lease(), {'released': True})

    def test_begin_leaves_existing_state_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            flow = Path(temporary)
            marker = flow / 'record.json'
            marker.write_text('{"containers": []}')
            with (
                patch.object(guest, 'FLOW', flow),
                patch.object(guest, 'load'),
                patch.object(guest, 'guard'),
                patch.object(guest, 'cleanup') as cleanup,
            ):
                with self.assertRaisesRegex(RuntimeError, 'choose recovery'):
                    guest.begin()
                cleanup.assert_not_called()
            self.assertEqual(marker.read_text(), '{"containers": []}')

    def test_initialization_failure_rolls_back_its_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            flow = Path(temporary) / 'flow'
            with (
                patch.object(guest, 'FLOW', flow),
                patch.object(guest, 'STATE', Path(temporary)),
                patch.object(guest, 'OVERRIDE', Path(temporary) / 'override'),
                patch.object(guest, 'load'),
                patch.object(guest, 'guard'),
                patch.object(guest, 'idle'),
                patch.object(guest, 'run', return_value=SimpleNamespace(stdout=b'')),
                patch.object(guest, 'request', return_value={'state': 'signed-in'}),
                patch.object(guest, 'wait_for_new_minute'),
                patch.object(guest.shutil, 'copy2', side_effect=OSError('disk full')),
                self.assertRaisesRegex(OSError, 'disk full'),
            ):
                guest.begin()
            self.assertFalse(flow.exists())

    def test_recover_partial_initialization_but_refuse_unknown_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            flow = root / 'flow'
            flow.mkdir()
            unknown = flow / 'unknown'
            unknown.touch()
            with (
                patch.object(guest, 'FLOW', flow),
                patch.object(guest, 'STATE', root),
                patch.object(guest, 'OVERRIDE', root / 'override'),
                patch.object(guest, 'idle'),
            ):
                with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                    guest.cleanup()
                self.assertTrue(unknown.exists())
                unknown.unlink()
                (flow / 'config.json').write_text('{}')
                self.assertEqual(guest.cleanup(), {'cleaned': True})
                self.assertFalse(flow.exists())

    def test_cleanup_failure_is_reported_and_can_be_retried_after_restoring_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            flow = root / 'flow'
            flow.mkdir()
            config = root / 'current.json'
            config.write_text('{"fixture": true}')
            (flow / 'fixture-config.json').write_text(config.read_text())
            (flow / 'config.json').write_text('{"original": true}')
            (flow / 'record.json').write_text('{"containers": ["already-removed"]}')
            override = root / 'override.conf'
            override.touch()

            def write_config(value: object) -> None:
                config.write_text(json.dumps(value))

            with (
                patch.object(guest, 'FLOW', flow),
                patch.object(guest, 'CONFIG', config),
                patch.object(guest, 'STATE', root),
                patch.object(guest, 'OVERRIDE', override),
                patch.object(guest, 'active', return_value=False),
                patch.object(guest, 'write_config', side_effect=write_config),
                patch.object(
                    guest,
                    'run',
                    side_effect=[
                        None,
                        SimpleNamespace(stdout=b''),
                        SimpleNamespace(stdout=b'image'),
                        RuntimeError('image busy'),
                    ],
                ),
                self.assertRaisesRegex(RuntimeError, 'image busy'),
            ):
                guest.cleanup()
            self.assertEqual(json.loads(config.read_text()), {'original': True})
            self.assertTrue(flow.exists())
            self.assertFalse(override.exists())
            with (
                patch.object(guest, 'FLOW', flow),
                patch.object(guest, 'CONFIG', config),
                patch.object(guest, 'STATE', root),
                patch.object(guest, 'OVERRIDE', override),
                patch.object(guest, 'active', return_value=False),
                patch.object(guest, 'write_config', side_effect=write_config),
                patch.object(guest, 'run', return_value=SimpleNamespace(stdout=b'')),
            ):
                self.assertEqual(guest.cleanup(), {'cleaned': True})
            self.assertFalse(flow.exists())

    def test_guard_rejects_non_fixture_destinations_sources_and_schedules(self):
        config, _ = configuration()
        config.update(enable=False, remotepath=guest.ROOT)
        config['sets'] = [
            {**config['sets'][0], 'name': name, 'paths': f'/data/interactive-fixtures/{name}'}
            for name in ('system', 'appData')
        ]
        with patch.object(Path, 'is_file', return_value=True):
            guest.guard(config)
            variants: tuple[Configuration, ...] = (
                {**config, 'remotepath': '/my-files/production'},
                {**config, 'enable': True},
                {**config, 'sets': []},
            )
            for variant in variants:
                with self.subTest(variant=variant), self.assertRaises(RuntimeError):
                    guest.guard(variant)
            config['sets'][0]['paths'] = '/etc'
            with self.assertRaisesRegex(RuntimeError, 'non-fixture'):
                guest.guard(config)

    def test_cleanup_does_not_overwrite_configuration_changed_by_someone_else(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / 'current.json'
            config.write_text('{"changed": true}')
            (root / 'fixture-config.json').write_text('{"fixture": true}')
            (root / 'config.json').write_text('{"original": true}')
            (root / 'record.json').write_text('{"containers": []}')
            with (
                patch.object(guest, 'FLOW', root),
                patch.object(guest, 'CONFIG', config),
                patch.object(guest, 'STATE', root),
                patch.object(guest, 'active', return_value=False),
                self.assertRaisesRegex(RuntimeError, 'Configuration changed'),
            ):
                guest.cleanup()
            self.assertEqual(json.loads(config.read_text()), {'changed': True})
            self.assertTrue((root / 'fixture-config.json').exists())

    def test_container_recovery_failure_preserves_fixture_and_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'recovery.json').write_text('{}')
            (root / 'record.json').write_text('{"containers": []}')
            with (
                patch.object(guest, 'FLOW', root),
                patch.object(guest, 'STATE', root),
                patch.object(guest, 'active', return_value=False),
                patch.object(guest, 'run', side_effect=RuntimeError('Docker failed')),
                self.assertRaisesRegex(RuntimeError, 'Docker failed'),
            ):
                guest.cleanup()
            self.assertTrue((root / 'recovery.json').exists())

    def test_restore_cannot_pass_using_the_previous_successful_run(self):
        with (
            patch.object(guest, 'idle'),
            patch.object(guest, 'record', return_value={'before': {'lastsuccess': 'old'}}),
            patch.object(guest, 'status', return_value={'phase': 'completed', 'lastsuccess': 'old'}),
            patch.object(guest, 'verify_backups') as verify,
            self.assertRaisesRegex(RuntimeError, 'new backup'),
        ):
            guest.verify()
        verify.assert_not_called()
