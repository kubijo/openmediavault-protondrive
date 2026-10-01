"""CLI adapter informed by ha-proton-drive-backup (MIT); strict destructive results."""

import json
import os
import re
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit

from .common import BINARY, BackupError


class SignedOut(BackupError):
    pass


def parse_json(text):
    # CLI streaming arrays can be preceded by diagnostics. Do not salvage an
    # arbitrary embedded object: it could be a fragment of a failed response.
    for index, line in enumerate(text.splitlines()):
        if line.lstrip().startswith(('[', '{')):
            try:
                return json.loads('\n'.join(text.splitlines()[index:]))
            except ValueError:
                continue
    raise BackupError('Unrecognized Proton JSON response; no cleanup performed')


def unwrap(value):
    if isinstance(value, dict):
        return value.get('value') if value.get('ok') is True else None
    return value


def entries(payload):
    if isinstance(payload, dict):
        for key in ('items', 'entries', 'children', 'files', 'nodes', 'results'):
            if key in payload:
                payload = payload[key]
                break
    if not isinstance(payload, list) or any(not isinstance(p, dict) for p in payload):
        raise BackupError('Unknown Proton listing shape')
    result = []
    for raw in payload:
        name, kind, uid = unwrap(raw.get('name')), unwrap(raw.get('type')), raw.get('uid')
        if not isinstance(name, str) or not name or '/' in name or '\\' in name or name in ('.', '..'):
            raise BackupError('Invalid remote entry name')
        if kind not in ('file', 'folder') or not isinstance(uid, str) or not uid:
            raise BackupError('Unknown remote entry identity or type')
        revision = unwrap(raw.get('activeRevision'))
        size = revision.get('claimedSize') if isinstance(revision, dict) else raw.get('size')
        result.append({'name': name, 'type': kind, 'uid': uid, 'size': size})
    return result


def node_result(payload, expected=None):
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or len(payload) != 1:
        raise BackupError('Ambiguous Proton operation result')
    item = payload[0]
    if not isinstance(item, dict) or item.get('ok') is not True or not isinstance(item.get('uid'), str):
        raise BackupError('Proton operation did not confirm success')
    if expected is not None and item['uid'] != expected:
        raise BackupError('Proton operation affected an unexpected UID')
    return item['uid']


class ProtonCli:
    def __init__(self, config, binary=BINARY):
        self.config = config
        self.binary = binary
        self.lock = threading.RLock()
        self.state_lock = threading.RLock()
        self.auth = {'state': 'unknown', 'url': '', 'error': ''}
        self.login = None
        self.current = None

    def _run(self, args, transfer=False, json_output=True):
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
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                raise BackupError('Proton operation timed out') from exc
            finally:
                self.current = None
            if proc.returncode:
                if 'You need to login first' in err or 'Failed to load session from secrets' in err:
                    self.auth = {'state': 'signed-out', 'url': '', 'error': ''}
                    raise SignedOut('Sign in to Proton Drive')
                # Raw diagnostics may contain tokens. Log operation and exit only.
                raise BackupError(f'Proton {args[0]} {args[1]} failed (exit {proc.returncode})')
            return parse_json(out) if json_output else out

    def cancel_transfer(self):
        proc = self.current
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def info(self, path):
        value = self._run(['filesystem', 'info', path])
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if not isinstance(value, dict) or not value:
            raise BackupError('No usable Proton item information')
        return value

    def probe(self):
        self.info('/my-files')
        self.auth = {'state': 'signed-in', 'url': '', 'error': ''}
        return dict(self.auth)

    def status(self):
        return dict(self.auth)

    def list(self, path):
        return entries(self._run(['filesystem', 'list', path]))

    def ensure_folder(self, path):
        parent = '/my-files'
        for name in path.split('/')[2:]:
            matches = [entry for entry in self.list(parent) if entry['name'] == name]
            if not matches:
                self._run(['filesystem', 'create-folder', parent, name])
                matches = [entry for entry in self.list(parent) if entry['name'] == name]
            if len(matches) != 1 or matches[0]['type'] != 'folder':
                raise BackupError('Remote destination is ambiguous')
            parent += '/' + name

    def upload(self, path, folder):
        self._run(
            ['filesystem', 'upload', '-f', 'skip', '-d', 'skip', '-t', str(path), folder],
            transfer=True,
            json_output=False,
        )

    def download(self, remote, directory):
        self._run(
            ['filesystem', 'download', '-f', 'skip', '-d', 'skip', remote, str(directory)],
            transfer=True,
            json_output=False,
        )

    def purge(self, folder, entry):
        matches = [e for e in self.list(folder) if e['name'] == entry['name']]
        if len(matches) != 1 or matches[0] != entry or entry['type'] != 'file':
            raise BackupError('Expired remote backup changed; cleanup skipped')
        uid = node_result(self._run(['filesystem', 'trash', folder + '/' + entry['name']]), entry['uid'])
        info = self.info('/trash/' + entry['name'])
        if info.get('uid') != uid or unwrap(info.get('type')) != 'file':
            raise BackupError('Expired backup left in trash: identity is ambiguous')
        node_result(self._run(['filesystem', 'delete', '/trash/' + entry['name']]), uid)

    def start_auth(self):
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

    def _watch_login(self, proc):
        import selectors

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
                    for match in re.findall(r'https://[^\s]+', line):
                        url = match.rstrip('.,;)\'"')
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
        except (BackupError, OSError, ValueError) as exc:
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

    def cancel_auth(self):
        with self.state_lock:
            proc, self.login = self.login, None
            if proc is not None:
                proc.kill()
                proc.wait()
            self.auth = {'state': 'unknown', 'url': '', 'error': ''}
        return self.status()

    def logout(self):
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
