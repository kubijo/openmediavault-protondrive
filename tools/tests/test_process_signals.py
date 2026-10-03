"""The persistent QEMU daemon must survive normal shell exit and terminal signals."""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vm_control import VMState


class SignalTests(unittest.TestCase):
    def wait_for(self, path, process):
        deadline = time.monotonic() + 10
        while not path.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail(f'Client did not reach {path.name}')
            time.sleep(0.01)

    def test_shell_exit_and_terminal_signals_leave_daemon_running(self):
        fixture = Path(__file__).parent / 'fixtures/vm_owner.py'
        for signum in (None, signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
                with (root / 'owner.log').open('w') as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            '-m',
                            'tools.tests.fixtures.vm_owner',
                            'exit' if signum is None else 'wait',
                            str(root),
                        ],
                        stdout=log,
                        stderr=log,
                        env=environment,
                        cwd=fixture.resolve().parents[3],
                    )
                    try:
                        if signum is not None:
                            self.wait_for(root / 'ready', process)
                            process.send_signal(signum)
                        self.assertEqual(process.wait(timeout=10), 0 if signum is None else 128 + signum)
                        self.assertTrue((root / 'instance/instance.json').exists())
                        self.assertIsNotNone(VMState(root).running())
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        VMState(root).stop(force=True, timeout=5)
