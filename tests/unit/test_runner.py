import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import configuration
from protondrive import runner
from protondrive.common import BackupError
from protondrive.completion import read_completion
from protondrive.json_data import decode, object_value
from protondrive.models import BackupSet


class RunnerTests(unittest.TestCase):
    def test_phase_changes_clear_stale_item_but_keep_run_details(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(runner, 'STATE', Path(temporary)):
            runner.status('uploading', message='archive.tar.zst', lastsuccess='previous')
            runner.status('retention')
            value = object_value(decode((Path(temporary) / 'status.json').read_text()))
            self.assertEqual(value['message'], '')
            self.assertEqual(value['lastsuccess'], 'previous')

    def exercise(self, fail: bool = False) -> None:
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            config, item = configuration()
            config['stagingpath'] = str(root / 'staging')
            item['stopcontainers'] = True
            events: list[str] = []

            def stop(timeout: int) -> None:
                events.append('stop')

            recovery = Mock(stop=stop, restore=lambda: events.append('restore'))

            def archive(item: BackupSet, path: Path, reserve: int) -> None:
                events.append('archive')
                path.write_bytes(b'partial')
                if fail:
                    raise BackupError('injected tar failure')

            def publish(item: BackupSet, path: Path, *args: object) -> Path:
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
            for name, value in replacements:
                stack.enter_context(patch.object(runner, name, value))
            stack.enter_context(patch('signal.signal'))
            stack.enter_context(patch('grp.getgrnam', return_value=SimpleNamespace(gr_gid=0)))
            stack.enter_context(patch('os.chown'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if fail:
                with self.assertRaisesRegex(BackupError, 'injected'):
                    runner.run()
                self.assertNotIn('upload', events)
                self.assertFalse(list((root / 'staging').rglob('*.partial')))
                self.assertEqual(events[-1], 'restore')
                self.assertIsNone(read_completion(root))
            else:
                runner.run()
                self.assertEqual(events, ['restore', 'stop', 'archive', 'restore', 'publish', 'upload'])
                completion = read_completion(root)
                assert completion is not None
                self.assertEqual(completion.generation, 1)
                status = object_value(decode((root / 'status.json').read_text()))
                self.assertEqual(completion.timestamp, status['lastsuccess'])

    def test_containers_restart_before_verification_and_upload(self):
        self.exercise()

    def test_archive_failure_restores_containers_and_never_uploads(self):
        self.exercise(fail=True)
