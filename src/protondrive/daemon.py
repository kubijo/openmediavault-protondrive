"""Unprivileged service inside the private D-Bus session."""

import json
import os
import signal
import socketserver
import stat
import threading
import time
import uuid
from pathlib import Path

from .common import LIMIT, SOCKET, STATE, BackupError
from .config import load, remote_folder
from .json_data import JSONValue, decode
from .models import Configuration
from .protoncli import ProtonCli
from .retention import prune_remote, upload_pair


def load_owner_id(path: Path = STATE / 'proton/owner-id') -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(str(uuid.uuid4()) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    value = path.read_text().strip()
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise BackupError('Invalid local Proton backup owner identity') from exc
    if parsed.version != 4 or str(parsed) != value:
        raise BackupError('Invalid local Proton backup owner identity')
    return value


class Service:
    def __init__(self, config: Configuration) -> None:
        self.config = config
        self.cli = ProtonCli(config, owner_id=load_owner_id())
        self.operations = threading.Lock()
        self.probe_guard = threading.Lock()
        self.last_probe = 0.0

    def schedule_probe(self) -> None:
        if self.operations.locked() or self.cli.login is not None or time.monotonic() - self.last_probe < 30:
            return
        if not self.probe_guard.acquire(blocking=False):
            return

        def probe() -> None:
            try:
                self.probe()
            finally:
                self.last_probe = time.monotonic()
                self.probe_guard.release()

        threading.Thread(target=probe, daemon=True).start()

    def probe(self) -> dict[str, str]:
        try:
            return self.cli.probe()
        except (BackupError, OSError, ValueError, TypeError) as exc:
            if self.cli.auth['state'] != 'signed-out':
                self.cli.auth['error'] = str(exc)
            return self.cli.status()

    def dispatch(self, message: dict[str, JSONValue]) -> object:
        operation = message.get('operation')
        if operation == 'status':
            self.schedule_probe()
            return {**self.cli.status(), **self.cli.transfer_status()}
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
            if operation not in ('prepare', 'upload', 'prune'):
                raise BackupError('Unknown Proton operation')
            item = next((item for item in self.config['sets'] if item['uuid'] == message.get('setuuid')), None)
            if item is None:
                raise BackupError('Unknown backup set')
            folder = remote_folder(self.config, item)
            path: Path | None = None
            if operation == 'upload':
                name = message.get('name', '')
                if not isinstance(name, str) or Path(name).name != name:
                    raise BackupError('Invalid archive name')
                if not name.endswith('.tar.zst'):
                    raise BackupError('Only completed archives may be uploaded')
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
            self.cli.ensure_instance_owned()
            if operation == 'prepare':
                self.cli.ensure_folder(folder)
                return self.cli.list(folder)
            if operation == 'upload':
                if path is None:
                    raise BackupError('Missing validated upload path')
                return upload_pair(self.cli, self.config, item, path, folder)
            return prune_remote(self.cli, self.config, item, folder)
        finally:
            self.operations.release()


def serve() -> None:
    # The CLI uses SIG_DFL for broken stdout pipes. A disconnected IPC client
    # must only fail its handler, never terminate the long-running daemon.
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    service = Service(load())

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            try:
                line = self.rfile.readline(LIMIT + 1)
                if len(line) > LIMIT:
                    raise BackupError('Request too large')
                message = decode(line)
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
