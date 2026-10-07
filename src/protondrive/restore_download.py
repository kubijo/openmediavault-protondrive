"""Read an archive pair without claiming or changing its remote directory."""

import re
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .archive import check_space
from .common import STATE, BackupError
from .config import identifier
from .json_data import JSONValue
from .models import Configuration, Manifest
from .operation import Cancelled, OperationControl
from .retention import RemoteFiles, archive_metadata, read_remote_manifest, unique

CACHE = STATE / 'proton/restore-cache'


def download(
    cli: RemoteFiles,
    config: Configuration,
    value: dict[str, JSONValue],
    cache: Path = CACHE,
    control: OperationControl | None = None,
) -> Manifest:
    control = control or OperationControl()
    instance, set_id, job = (identifier(value.get(key)) for key in ('instanceuuid', 'setuuid', 'jobuuid'))
    name = value.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}-\d{8}T\d{4}Z\.tar\.zst', name):
        raise BackupError('Invalid archive name')
    folder = f'{config["remotepath"]}/{instance}/{set_id}'
    control.check()
    listing = cli.list(folder)
    archive = unique(listing, name)
    unique(listing, name + '.manifest.json')
    control.check()
    manifest = archive_metadata(read_remote_manifest(cli, folder, name + '.manifest.json'), instance, set_id, name)
    if archive['size'] != manifest['size']:
        raise BackupError('Remote size disagrees with the completion manifest')
    cache.mkdir(mode=0o700, exist_ok=True)
    directory = cache / job
    directory.mkdir(mode=0o700)
    try:
        check_space(directory, manifest['size'], config['minimumfreebytes'])
        control.check()
        cli.download(folder + '/' + name, directory)
        control.check()
        return manifest
    except BaseException:
        shutil.rmtree(directory)
        raise


def discard(job: JSONValue, cache: Path = CACHE) -> None:
    directory = cache / identifier(job)
    if directory.is_symlink():
        raise BackupError('Restore cache is not a directory')
    if directory.exists():
        shutil.rmtree(directory)


class Downloads:
    """Scope cancellation to an inspection, including cancellation before admission."""

    def __init__(self, cancel_transfer: Callable[[bool], None]) -> None:
        self._guard = threading.Lock()
        self._cancelled: deque[str] = deque(maxlen=64)
        self._active: dict[str, OperationControl] = {}
        self._cancel_transfer = cancel_transfer

    def cancel(self, value: JSONValue) -> None:
        job = identifier(value)
        with self._guard:
            if job not in self._cancelled:
                self._cancelled.append(job)
            control = self._active.get(job)
            if control is not None:
                control.cancel()

    def run(
        self, cli: RemoteFiles, config: Configuration, value: dict[str, JSONValue], cache: Path = CACHE
    ) -> Manifest:
        job = identifier(value.get('jobuuid'))
        control = OperationControl()
        with self._guard:
            if job in self._cancelled:
                raise Cancelled('Archive download cancelled')
            self._active[job] = control
        finished = threading.Event()

        def watch() -> None:
            cancelled_at: float | None = None
            while not finished.wait(0.1):
                try:
                    control.check()
                except Cancelled:
                    # Repeat until the operation exits: cancellation can race
                    # the transition between manifest and archive subprocesses.
                    cancelled_at = cancelled_at or time.monotonic()
                    self._cancel_transfer(time.monotonic() - cancelled_at >= 2)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            return download(cli, config, value, cache=cache, control=control)
        finally:
            finished.set()
            watcher.join()
            with self._guard:
                del self._active[job]
