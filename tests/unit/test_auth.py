import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from helpers import configuration

from protondrive.protoncli import ProtonCli


class AuthTests(unittest.TestCase):
    def client(self, delay):
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

    def wait_for(self, predicate):
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

    def test_cancel_reaps_login_and_clears_url(self):
        cli = self.client(10)
        cli.start_auth()
        self.wait_for(lambda: bool(cli.status()['url']))
        proc = cli.login
        cli.cancel_auth()
        self.assertIsNotNone(proc.poll())
        self.assertEqual(cli.status()['url'], '')
        self.assertIsNone(cli.login)
