"""Render the shipped Monit checks and deliver alerts to a loopback-only SMTP sink."""

import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ('backup', 'recovery', 'auth', 'service')


def render_checks(enabled: tuple[str, ...], email: bool = True) -> str:
    env = Environment(undefined=StrictUndefined)
    env.filters['to_bool'] = bool
    return env.from_string((ROOT / 'src/salt/files/monit.conf.j2').read_text()).render(
        email_config={'enable': email, 'primaryemail': 'admin@example.invalid'},
        notifications=[{'id': 'protondrive' + name, 'enable': True} for name in enabled],
    )


class Mailbox:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.messages: list[bytes] = []

    def append(self, data: bytes) -> None:
        with self.lock:
            self.messages.append(data)

    def snapshot(self) -> list[bytes]:
        with self.lock:
            return list(self.messages)


def smtp_handler(mailbox: Mailbox) -> type[socketserver.StreamRequestHandler]:
    class Handler(socketserver.StreamRequestHandler):
        connection: socket.socket

        def handle(self) -> None:
            self.connection.settimeout(5)
            self.wfile.write(b'220 localhost fixture\r\n')
            while line := self.rfile.readline():
                verb = line.split(b' ', 1)[0].strip().upper()
                if verb == b'DATA':
                    self.wfile.write(b'354 Continue\r\n')
                    chunks: list[bytes] = []
                    while (chunk := self.rfile.readline()) not in (b'.\r\n', b''):
                        chunks.append(chunk)
                    mailbox.append(b''.join(chunks))
                elif verb == b'QUIT':
                    self.wfile.write(b'221 Bye\r\n')
                    return
                elif verb not in (b'HELO', b'EHLO', b'MAIL', b'RCPT', b'RSET'):
                    raise ValueError(f'Unexpected fixture SMTP command: {verb!r}')
                self.wfile.write(b'250 OK\r\n')

    return Handler


class NotificationTests(unittest.TestCase):
    def test_categories_are_opt_in_and_follow_the_global_email_switch(self) -> None:
        self.assertEqual(render_checks(()).count('noalert admin@example.invalid'), 4)
        self.assertEqual(render_checks(('backup',)).count('noalert admin@example.invalid'), 3)
        self.assertNotIn('noalert', render_checks(CHECKS))
        self.assertNotIn('noalert', render_checks((), email=False))
        self.assertNotIn('reminder', render_checks(CHECKS))

    def test_native_monit_delivery_deduplicates_and_reports_recovery(self) -> None:
        monit = shutil.which('monit')
        self.assertIsNotNone(monit, 'Monit must be provided by the repository test environment')
        assert monit is not None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'observations.json'
            mailbox = Mailbox()

            def observations(*failed: str) -> None:
                temporary = state.with_suffix('.new')
                temporary.write_text(json.dumps({name: name in failed for name in CHECKS}))
                temporary.replace(state)

            observations()
            with socketserver.TCPServer(('127.0.0.1', 0), smtp_handler(mailbox)) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    config = root / 'monitrc'
                    fixture = ROOT / 'tools/tests/fixtures/monit_health.py'
                    checks = render_checks(('backup',)).replace(
                        '/usr/sbin/omv-protondrive health', f'{sys.executable} {fixture} {state}'
                    )
                    config.write_text(
                        f'set daemon 1\nset pidfile {root}/pid\nset statefile {root}/state\n'
                        f'set idfile {root}/id\nset logfile {root}/log\n'
                        f'set mailserver 127.0.0.1 port {server.server_address[1]}\n'
                        'set alert admin@example.invalid only on { status }\n' + checks
                    )
                    config.chmod(0o600)
                    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'tools')}
                    syntax = subprocess.run(
                        [monit, '-c', str(config), '-t'], capture_output=True, text=True, check=False
                    )
                    self.assertEqual(syntax.returncode, 0, syntax.stdout + syntax.stderr)
                    with (root / 'stderr').open('w') as stderr:
                        proc = subprocess.Popen([monit, '-I', '-c', str(config)], env=env, stdout=stderr, stderr=stderr)
                        try:

                            def wait_for(predicate: Callable[[], bool]) -> None:
                                deadline = time.monotonic() + 15
                                while time.monotonic() < deadline:
                                    if predicate():
                                        return
                                    self.assertIsNone(proc.poll(), (root / 'stderr').read_text())
                                    time.sleep(0.1)
                                self.fail((root / 'log').read_text())

                            observations('backup', 'auth')
                            wait_for(lambda: len(mailbox.snapshot()) >= 1)
                            time.sleep(3)
                            messages = mailbox.snapshot()
                            self.assertEqual(len(messages), 1, messages)
                            self.assertIn(b'protondrive-backup', messages[0])
                            self.assertIn(b'Fixture incident', messages[0])
                            observations('auth')
                            wait_for(lambda: len(mailbox.snapshot()) >= 2)
                            time.sleep(3)
                            messages = mailbox.snapshot()
                            self.assertEqual(len(messages), 2, messages)
                            self.assertIn(b'protondrive-backup', messages[1])
                            self.assertIn(b'Status succeeded', messages[1])
                            observations('backup', 'auth')
                            wait_for(lambda: len(mailbox.snapshot()) >= 3)
                            self.assertIn(b'Fixture incident', mailbox.snapshot()[2])
                        finally:
                            proc.terminate()
                            proc.wait(timeout=10)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
