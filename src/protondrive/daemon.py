"""Unprivileged service inside the private D-Bus session."""

import json
import logging
import signal
import socketserver
import stat
import threading
import time
import uuid
from pathlib import Path

from .backend import CliLockRepair, StorageBackend
from .backend_registry import create_backend
from .common import LIMIT, SOCKET, BackupError
from .config import identifier, load, remote_folder
from .json_data import JSONValue, decode
from .models import AuthStatus, Configuration, Destination
from .restore_download import Downloads, discard
from .retention import prune_remote, upload_pair

logger = logging.getLogger(__name__)


class Service:
    def __init__(self, config: Configuration) -> None:
        self.config = config
        self.backends: dict[str, StorageBackend] = {
            destination['id']: create_backend(destination, config) for destination in config['destinations']
        }
        self.downloads = {
            destination_id: Downloads(backend.cancel_transfer) for destination_id, backend in self.backends.items()
        }
        self.operations = threading.Lock()
        self.probe_guard = threading.Lock()
        self.last_probe = 0.0

    def destination(self, message: dict[str, JSONValue]) -> tuple[Destination, StorageBackend]:
        default_id = next(
            (entry['id'] for entry in self.config['destinations'] if entry['id'] == 'protondrive'),
            self.config['destinations'][0]['id'],
        )
        destination_id = message.get('destinationid', default_id)
        if not isinstance(destination_id, str):
            raise BackupError('Invalid backup destination')
        destination = next((entry for entry in self.config['destinations'] if entry['id'] == destination_id), None)
        if destination is None:
            raise BackupError('Unknown backup destination')
        return destination, self.backends[destination_id]

    def schedule_probe(self) -> None:
        if self.operations.locked() or time.monotonic() - self.last_probe < 30:
            return
        if not self.probe_guard.acquire(blocking=False):
            return

        def probe() -> None:
            try:
                for destination in self.config['destinations']:
                    if not self.backends[destination['id']].authentication_in_progress():
                        self.probe(destination['id'])
            finally:
                self.last_probe = time.monotonic()
                self.probe_guard.release()

        threading.Thread(target=probe, daemon=True).start()

    def probe(self, destination_id: str = 'protondrive') -> AuthStatus:
        backend = self.backends[destination_id]
        try:
            return backend.probe()
        except BackupError as exc:
            return backend.probe_failed(str(exc))
        except Exception:
            reference = uuid.uuid4().hex[:12]
            logger.exception('Backend probe failed; reference %s', reference)
            return backend.probe_failed(f'Account status unavailable; reference {reference}')

    def dispatch(self, message: dict[str, JSONValue]) -> object:
        operation = message.get('operation')
        destination, backend = self.destination(message)
        if operation == 'status':
            self.schedule_probe()
            return {**backend.status(), **backend.transfer_status()}
        if operation == 'backend-statuses':
            self.schedule_probe()
            return [
                {
                    'id': entry['id'],
                    'kind': entry['kind'],
                    'name': entry['name'],
                    'enable': entry['enable'],
                    **self.backends[entry['id']].status(),
                    **self.backends[entry['id']].transfer_status(),
                }
                for entry in self.config['destinations']
            ]
        if operation == 'cancel-transfer':
            targets = self.backends.values() if 'destinationid' not in message else (backend,)
            for target in targets:
                target.cancel_transfer()
            return True
        if operation == 'cancel-download':
            for download in self.downloads.values():
                download.cancel(message.get('jobuuid'))
            return True
        if operation == 'start-auth':
            return backend.start_auth()
        if operation == 'cancel-auth':
            return backend.cancel_auth()
        if operation == 'logout':
            return backend.logout()
        # Navigation overlaps reads; wait briefly within the API's 25-second deadline.
        if not self.operations.acquire(timeout=5 if operation == 'browse' else 0):
            raise BackupError('Storage service is busy; retry after the current operation', code='busy')
        try:
            if operation == 'repair-cli-lock':
                if not isinstance(backend, CliLockRepair):
                    raise BackupError('This backend has no CLI lock to repair')
                return backend.repair_lock()
            if operation == 'probe':
                return self.probe(destination['id'])
            if operation == 'download-archive':
                scoped = self.config.copy()
                scoped['remotepath'] = destination['root']
                return self.downloads[destination['id']].run(backend, scoped, message)
            if operation == 'discard-download':
                discard(message.get('jobuuid'))
                return True
            if operation == 'browse':
                # Browsing must never create folders, claim an instance or run
                # retention, including when reading a different NAS's archives.
                folder = destination['root']
                instance = message.get('instanceuuid')
                set_id = message.get('setuuid')
                if set_id is not None and instance is None:
                    raise BackupError('Browsing a backup set requires an instance')
                if instance is not None:
                    folder += '/' + identifier(instance)
                if set_id is not None:
                    folder += '/' + identifier(set_id)
                return backend.list(folder)
            if operation not in ('prepare', 'upload', 'prune'):
                raise BackupError('Unknown storage operation')
            item = next((item for item in self.config['sets'] if item['uuid'] == message.get('setuuid')), None)
            if item is None:
                raise BackupError('Unknown backup set')
            folder = remote_folder(self.config, item, destination)
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
            backend.ensure_instance_owned()
            if operation == 'prepare':
                backend.ensure_folder(folder)
                return backend.list(folder)
            if operation == 'upload':
                if path is None:
                    raise BackupError('Missing validated upload path')
                return upload_pair(backend, self.config, item, path, folder)
            return prune_remote(backend, self.config, item, folder)
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
            except BackupError as exc:
                result = {'ok': False, 'code': exc.code, 'error': str(exc)}
            except Exception:
                reference = uuid.uuid4().hex[:12]
                logger.exception('Proton service failure %s', reference)
                result = {'ok': False, 'code': 'internal', 'error': f'Proton service failed; reference {reference}'}
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
