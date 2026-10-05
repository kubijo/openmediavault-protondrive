"""CLI adapter informed by ha-proton-drive-backup (MIT); strict destructive results."""

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .common import BINARY, STATE, BackupError, atomic_json, sync_directory
from .json_data import JSONValue, decode
from .models import Configuration, RemoteEntry, TransferStatus

OWNER_MARKER = '.omv-protondrive-owner.json'


class SignedOut(BackupError):
    pass


def parse_json(text: str) -> JSONValue:
    # CLI streaming arrays can be preceded by diagnostics. Do not salvage an
    # arbitrary embedded object: it could be a fragment of a failed response.
    for index, line in enumerate(text.splitlines()):
        if line.lstrip().startswith(('[', '{')):
            try:
                return decode('\n'.join(text.splitlines()[index:]))
            except ValueError:
                continue
    raise BackupError('Unrecognized Proton JSON response; no cleanup performed')


def unwrap(value: JSONValue) -> JSONValue:
    if isinstance(value, dict) and 'ok' in value:
        return value.get('value') if value['ok'] is True else None
    return value


def entries(payload: JSONValue) -> list[RemoteEntry]:
    if isinstance(payload, dict):
        for key in ('items', 'entries', 'children', 'files', 'nodes', 'results'):
            if key in payload:
                payload = payload[key]
                break
    if not isinstance(payload, list) or any(not isinstance(p, dict) for p in payload):
        raise BackupError('Unknown Proton listing shape')
    result: list[RemoteEntry] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise BackupError('Unknown Proton listing shape')
        name, kind, uid = unwrap(raw.get('name')), unwrap(raw.get('type')), raw.get('uid')
        if not isinstance(name, str) or not name or '/' in name or '\\' in name or name in ('.', '..'):
            raise BackupError('Invalid remote entry name')
        if not isinstance(kind, str) or kind not in ('file', 'folder') or not isinstance(uid, str) or not uid:
            raise BackupError('Unknown remote entry identity or type')
        revision = unwrap(raw.get('activeRevision'))
        size = revision.get('claimedSize') if isinstance(revision, dict) else raw.get('size')
        if size is not None and type(size) is not int:
            raise BackupError('Invalid remote entry size')
        result.append({'name': name, 'type': kind, 'uid': uid, 'size': size})
    return result


def node_result(payload: JSONValue, expected: str | None = None) -> str:
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or len(payload) != 1:
        raise BackupError('Ambiguous Proton operation result')
    item = payload[0]
    if not isinstance(item, dict) or item.get('ok') is not True:
        raise BackupError('Proton operation did not confirm success')
    uid = item.get('uid')
    if not isinstance(uid, str):
        raise BackupError('Proton operation did not confirm success')
    if expected is not None and uid != expected:
        raise BackupError('Proton operation affected an unexpected UID')
    return uid


class ProtonCli:
    def __init__(
        self,
        config: Configuration,
        binary: str | Path = BINARY,
        *,
        owner_id: str | None = None,
        claims_directory: str | Path = STATE / 'proton/pending-claims',
    ) -> None:
        self.config = config
        self.binary = binary
        self.owner_id = owner_id
        self.claims_directory = Path(claims_directory)
        self.lock = threading.RLock()
        self.state_lock = threading.RLock()
        self.auth: dict[str, str] = {'state': 'unknown', 'url': '', 'error': ''}
        self.login: subprocess.Popen[str] | None = None
        self.current: subprocess.Popen[str] | None = None
        self.transfer: tuple[str, str, float] | None = None

    def _managed_path(self, path: object, *, allow_root: bool = False) -> None:
        root = self.config['remotepath']
        if not isinstance(path, str) or not ((allow_root and path == root) or path.startswith(root + '/')):
            raise BackupError('Proton storage operation outside the configured root folder')
        if any(
            part in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9 _.-]+', part)
            for part in path[len(root) :].split('/')[1:]
        ):
            raise BackupError('Invalid Proton storage path')

    def check_storage(self, args: list[str]) -> None:
        if args[0] != 'filesystem':
            return
        operation = args[1] if len(args) > 1 else ''
        if operation in ('info', 'list') and len(args) == 3:
            if args[2] != '/my-files':
                self._managed_path(args[2], allow_root=True)
        elif operation == 'create-folder' and len(args) == 4:
            if args[2] != '/my-files':
                self._managed_path(args[2], allow_root=True)
            self._managed_path(args[2] + '/' + args[3], allow_root=True)
        elif operation == 'upload' and len(args) == 9 and args[2:7] == ['-f', 'skip', '-d', 'skip', '-t']:
            self._managed_path(args[8])
        elif operation == 'download' and len(args) == 8 and args[2:6] == ['-f', 'skip', '-d', 'skip']:
            self._managed_path(args[6])
        elif operation == 'trash' and len(args) == 3:
            self._managed_path(args[2])
        else:
            raise BackupError('Unsupported Proton storage operation')

    def _run(
        self,
        args: list[str],
        transfer: bool = False,
        json_output: bool = True,
        activity: tuple[str, str] | None = None,
    ) -> JSONValue:
        self.check_storage(args)
        with self.lock:
            if self.login is not None:
                raise BackupError('Sign-in is in progress')
            command = [self.binary, *args, *(['--json'] if json_output else [])]
            timeout = self.config['transfertimeout' if transfer else 'commandtimeout']
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            self.current = proc
            self.transfer = (*activity, time.monotonic()) if activity else None
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                raise BackupError('Proton operation timed out') from exc
            finally:
                self.transfer = None
                self.current = None
            if proc.returncode:
                if 'You need to login first' in err or 'Failed to load session from secrets' in err:
                    self.auth = {'state': 'signed-out', 'url': '', 'error': ''}
                    raise SignedOut('Sign in to Proton Drive')
                # Raw diagnostics may contain tokens. Log operation and exit only.
                raise BackupError(f'Proton {args[0]} {args[1]} failed (exit {proc.returncode})')
            return parse_json(out) if json_output else out

    def transfer_status(self) -> TransferStatus:
        transfer = self.transfer
        if transfer is None:
            return {'transferphase': '', 'transferfile': '', 'transferelapsed': 0}
        phase, name, started = transfer
        return {
            'transferphase': phase,
            'transferfile': name,
            'transferelapsed': max(0, int(time.monotonic() - started)),
        }

    def cancel_transfer(self) -> None:
        proc = self.current
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def info(self, path: str) -> dict[str, JSONValue]:
        value = self._run(['filesystem', 'info', path])
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if not isinstance(value, dict) or not value:
            raise BackupError('No usable Proton item information')
        return value

    def probe(self) -> dict[str, str]:
        previous = self.auth
        root = self.info('/my-files')
        owner = root.get('ownedBy')
        owner = owner if isinstance(owner, dict) else {}
        # Publish only display metadata, never the complete CLI response.
        identity: dict[str, str] = {}
        for field in ('email', 'organization'):
            value = owner.get(field)
            identity[field] = value.strip() if isinstance(value, str) else ''
        with self.state_lock:
            # A completed probe must not restore identity after logout/cancellation.
            if self.auth is previous:
                self.auth = {'state': 'signed-in', 'url': '', 'error': '', **identity}
            return self.status()

    def status(self) -> dict[str, str]:
        return {'email': '', 'organization': '', **self.auth}

    def list(self, path: str) -> list[RemoteEntry]:
        return entries(self._run(['filesystem', 'list', path]))

    def ensure_folder(self, path: str) -> None:
        self._managed_path(path, allow_root=True)
        parent = '/my-files'
        for name in path.split('/')[2:]:
            matches = [entry for entry in self.list(parent) if entry['name'] == name]
            if not matches:
                self._run(['filesystem', 'create-folder', parent, name])
                matches = [entry for entry in self.list(parent) if entry['name'] == name]
            if len(matches) != 1 or matches[0]['type'] != 'folder':
                raise BackupError('Remote destination is ambiguous')
            parent += '/' + name

    def ensure_instance_owned(self) -> None:
        if not isinstance(self.owner_id, str) or not self.owner_id:
            raise BackupError('Local Proton backup owner identity is missing')
        folder = self.config['remotepath'] + '/' + self.config['instanceuuid']
        pending = self.claims_directory / (hashlib.sha256(folder.encode()).hexdigest() + '.json')
        if pending.exists() or pending.is_symlink():
            raise BackupError('Remote backup ownership claim is incomplete; inspect the pending claim before retrying')
        self.ensure_folder(folder)
        listing = self.list(folder)
        matches = [entry for entry in listing if entry['name'] == OWNER_MARKER]
        if len(matches) > 1 or (matches and matches[0]['type'] != 'file'):
            raise BackupError('Remote backup instance owner marker is ambiguous')
        expected = {'format': 1, 'instanceuuid': self.config['instanceuuid'], 'ownerid': self.owner_id}
        if not matches:
            if listing:
                raise BackupError('Remote backup instance has data but no owner marker; refusing to claim it')
            # Record intent durably before publishing our marker. A failed claim
            # must not become trusted merely because a retry sees that marker.
            self.claims_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            sync_directory(self.claims_directory.parent)
            atomic_json(pending, {'folder': folder, **expected})
            with tempfile.TemporaryDirectory(prefix='omv-proton-owner-') as temporary:
                marker = Path(temporary) / OWNER_MARKER
                marker.write_text(json.dumps(expected, sort_keys=True) + '\n')
                self.upload(marker, folder)
            claimed = self.list(folder)
            if len(claimed) != 1 or claimed[0]['name'] != OWNER_MARKER or claimed[0]['type'] != 'file':
                raise BackupError('Remote backup instance changed during ownership claim')
        with tempfile.TemporaryDirectory(prefix='omv-proton-owner-') as temporary:
            self.download(folder + '/' + OWNER_MARKER, temporary)
            marker = Path(temporary) / OWNER_MARKER
            if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
                raise BackupError('Invalid remote backup instance owner marker')
            try:
                actual = decode(marker.read_text())
            except (OSError, ValueError) as exc:
                raise BackupError('Invalid remote backup instance owner marker') from exc
        if actual != expected:
            raise BackupError('Remote backup instance belongs to a different installation')
        if not matches:
            pending.unlink()
            sync_directory(self.claims_directory)

    def upload(self, path: str | Path, folder: str) -> None:
        name = Path(path).name
        phase = 'Manifest upload' if name.endswith('.manifest.json') else 'Archive upload'
        if name == OWNER_MARKER:
            phase = 'Ownership marker upload'
        self._run(
            ['filesystem', 'upload', '-f', 'skip', '-d', 'skip', '-t', str(path), folder],
            transfer=True,
            json_output=False,
            activity=(phase, name),
        )

    def download(self, remote: str, directory: str | Path) -> None:
        name = Path(remote).name
        phase = 'Manifest download' if name.endswith('.manifest.json') else 'Verification download'
        if name == OWNER_MARKER:
            phase = 'Ownership marker download'
        self._run(
            ['filesystem', 'download', '-f', 'skip', '-d', 'skip', remote, str(directory)],
            transfer=True,
            json_output=False,
            activity=(phase, name),
        )

    def trash(self, folder: str, entry: RemoteEntry) -> None:
        matches = [e for e in self.list(folder) if e['name'] == entry['name']]
        if len(matches) != 1 or matches[0] != entry or entry['type'] != 'file':
            raise BackupError('Expired remote backup changed; cleanup skipped')
        node_result(self._run(['filesystem', 'trash', folder + '/' + entry['name']]), entry['uid'])

    def start_auth(self) -> dict[str, str]:
        with self.state_lock:
            if self.login is not None:
                return self.status()
            if not self.lock.acquire(blocking=False):
                raise BackupError('A Proton operation is in progress')
            try:
                proc = subprocess.Popen(
                    [self.binary, 'auth', 'login'],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                self.login = proc
                self.auth = {'state': 'signing-in', 'url': '', 'error': ''}
            finally:
                self.lock.release()
            threading.Thread(target=self._watch_login, args=(proc,), daemon=True).start()
            return self.status()

    def _watch_login(self, proc: subprocess.Popen[str]) -> None:
        import selectors

        if proc.stdout is None:
            raise BackupError('Sign-in output stream is missing')
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline, buffer = time.monotonic() + 600, ''
        try:
            while proc.poll() is None and time.monotonic() < deadline:
                if not selector.select(timeout=0.5):
                    continue
                chunk = os.read(proc.stdout.fileno(), 8192).decode(errors='replace')
                buffer = (buffer + chunk)[-32768:]
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    for match in re.finditer(r'https://[^\s]+', line):
                        url = match.group().rstrip('.,;)\'"')
                        parsed = urlsplit(url)
                        if (
                            parsed.netloc == 'account.proton.me'
                            and parsed.path == '/desktop/login'
                            and parsed.fragment
                            and self.login is proc
                        ):
                            self.auth['url'] = url
            with self.state_lock:
                if self.login is not proc:
                    return
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                    raise BackupError('Sign-in timed out')
                if proc.returncode:
                    raise BackupError('Sign-in did not complete')
                self.login = None
                self.probe()
        except (BackupError, OSError, ValueError, TypeError) as exc:
            if self.login is proc or self.login is None:
                self.auth = {'state': 'error', 'url': '', 'error': str(exc)}
        finally:
            selector.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()
            if self.login is proc:
                self.login = None

    def cancel_auth(self) -> dict[str, str]:
        with self.state_lock:
            proc, self.login = self.login, None
            if proc is not None:
                proc.kill()
                proc.wait()
            self.auth = {'state': 'unknown', 'url': '', 'error': ''}
        return self.status()

    def logout(self) -> dict[str, str]:
        # The same state -> CLI lock order is used by login completion.
        with self.state_lock:
            if not self.lock.acquire(blocking=False):
                raise BackupError('A Proton operation is in progress')
            try:
                self.cancel_auth()
                self._run(['auth', 'logout'], json_output=False)
                self.auth = {'state': 'signed-out', 'url': '', 'error': ''}
                return self.status()
            finally:
                self.lock.release()
