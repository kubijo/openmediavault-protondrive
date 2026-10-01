"""Unprivileged service inside the private D-Bus session."""

import json
import socketserver
import stat
import threading
import time
from pathlib import Path

from .common import LIMIT, SOCKET, BackupError
from .config import load, remote_folder
from .protoncli import ProtonCli
from .retention import prune_remote, upload_pair


class Service:
    def __init__(self, config):
        self.config = config
        self.cli = ProtonCli(config)
        self.operations = threading.Lock()
        self.probe_guard = threading.Lock()
        self.last_probe = 0

    def schedule_probe(self):
        if self.operations.locked() or self.cli.login is not None or time.monotonic() - self.last_probe < 30:
            return
        if not self.probe_guard.acquire(blocking=False):
            return

        def probe():
            try:
                self.probe()
            finally:
                self.last_probe = time.monotonic()
                self.probe_guard.release()

        threading.Thread(target=probe, daemon=True).start()

    def probe(self):
        try:
            return self.cli.probe()
        except (BackupError, OSError, ValueError) as exc:
            if self.cli.auth['state'] != 'signed-out':
                self.cli.auth['error'] = str(exc)
            return self.cli.status()

    def dispatch(self, message):
        operation = message.get('operation')
        if operation == 'status':
            self.schedule_probe()
            return self.cli.status()
        if operation == 'cancel-transfer':
            self.cli.cancel_transfer()
            return True
        if operation == 'start-auth':
            return self.cli.start_auth()
        if operation == 'cancel-auth':
            return self.cli.cancel_auth()
        if operation == 'logout':
            return self.cli.logout()
        if not self.operations.acquire(blocking=False):
            raise BackupError('Proton service is busy')
        try:
            if operation == 'probe':
                return self.cli.probe()
            item = next((item for item in self.config['sets'] if item['uuid'] == message.get('setuuid')), None)
            if item is None:
                raise BackupError('Unknown backup set')
            folder = remote_folder(self.config, item)
            if operation == 'prepare':
                self.cli.ensure_folder(folder)
                return self.cli.list(folder)
            if operation == 'upload':
                name = message.get('name', '')
                if not isinstance(name, str) or Path(name).name != name:
                    raise BackupError('Invalid archive name')
                directory = Path(self.config['stagingpath']) / item['uuid']
                path = directory / name
                for candidate in (path, path.with_name(name + '.manifest.json')):
                    st = candidate.lstat()
                    if (
                        not stat.S_ISREG(st.st_mode)
                        or st.st_uid != 0
                        or candidate.resolve().parent != directory.resolve()
                    ):
                        raise BackupError('Uploads require root-owned completed staging files')
                if not name.endswith('.tar.zst'):
                    raise BackupError('Only completed archives may be uploaded')
                return upload_pair(self.cli, self.config, item, path, folder)
            if operation == 'prune':
                return prune_remote(self.cli, self.config, item, folder)
            raise BackupError('Unknown Proton operation')
        finally:
            self.operations.release()


def serve():
    service = Service(load())

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                line = self.rfile.readline(LIMIT + 1)
                if len(line) > LIMIT:
                    raise BackupError('Request too large')
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise BackupError('Invalid request')
                result = {'ok': True, 'result': service.dispatch(message)}
            except Exception as exc:  # noqa: BLE001 -- isolate failures at the IPC boundary
                result = {'ok': False, 'error': str(exc)}
            try:
                self.wfile.write(json.dumps(result).encode() + b'\n')
            except BrokenPipeError:
                pass

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    SOCKET.unlink(missing_ok=True)
    with Server(str(SOCKET), Handler) as server:
        SOCKET.chmod(0o600)
        service.schedule_probe()
        server.serve_forever()
