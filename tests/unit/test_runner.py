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


class RunnerTests(unittest.TestCase):
    def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            config, item = configuration()
            config['stagingpath'] = str(root / 'staging')
            item['stopcontainers'] = True
            events = []
            recovery = Mock()
            recovery.stop.side_effect = lambda *args: events.append('stop')
            recovery.restore.side_effect = lambda: events.append('restore')

            def archive(item, path, reserve):
                events.append('archive')
                path.write_bytes(b'partial')
                if fail:
                    raise BackupError('injected tar failure')

            def publish(item, path, *args):
                events.append('publish')
                final = path.with_suffix('')
                path.rename(final)
                return final

            for name, value in [
                ('STATE', root),
                ('load', lambda: config),
                ('Recovery', lambda: recovery),
                ('estimate', lambda item: 1),
                ('check_space', Mock()),
                ('archive', archive),
                ('publish', publish),
                ('prune_local', Mock()),
                ('request', lambda *a, **kw: []),
                ('upload', lambda *a: events.append('upload')),
            ]:
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
            else:
                runner.run()
                self.assertEqual(events, ['restore', 'stop', 'archive', 'restore', 'publish', 'upload'])

    def test_containers_restart_before_verification_and_upload(self):
        self.exercise()

    def test_archive_failure_restores_containers_and_never_uploads(self):
        self.exercise(fail=True)
