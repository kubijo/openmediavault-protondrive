"""Bounded extraction holds and controller crashes owned by one development probe."""

import os
import subprocess
import time
from pathlib import Path

from protondrive.common import STATE, locked, sync_directory

UNIT = 'omv-protondrive-controller.service'
OVERRIDE = Path(f'/run/systemd/system/{UNIT}.d/90-owned-restore.conf')
TEMPLATE = Path(__file__).with_name('owned-restore.conf')
SOCKET = Path('/run/omv-protondrive-controller/control.sock')


def run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=60)


def restart() -> None:
    before = SOCKET.stat() if SOCKET.exists() else None
    run('systemctl', 'restart', UNIT)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            after = SOCKET.stat()
            if before is None or (before.st_ino, before.st_ctime_ns) != (after.st_ino, after.st_ctime_ns):
                run('systemctl', 'is-active', UNIT)
                return
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    raise RuntimeError('Controller did not return; restore fixture retained')


def active(directory: Path) -> Path:
    path = directory.parent / 'active'
    if path.is_symlink() or path.read_text() != directory.name + '\n':
        raise RuntimeError('Another probe owns the extraction hold')
    return path


def arm(directory: Path) -> dict[str, bool]:
    with locked(STATE / 'run.lock'):
        if OVERRIDE.exists() or OVERRIDE.is_symlink():
            raise RuntimeError('An existing controller override requires recovery')
        with (directory.parent / 'active').open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(directory.name + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(directory.parent)
        (directory / 'held').unlink(missing_ok=True)
        OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
        with OVERRIDE.open('xb') as stream:
            stream.write(TEMPLATE.read_bytes())
    run('systemctl', 'daemon-reload')
    restart()
    return {'armed': True}


def held(directory: Path) -> dict[str, bool]:
    active(directory)
    deadline = time.monotonic() + 20
    while not (directory / 'held').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Extraction did not reach its hold')
        time.sleep(0.1)
    return {'held': True}


def crash(directory: Path) -> dict[str, bool]:
    held(directory)
    run('systemctl', 'kill', '--kill-whom=all', '--signal=SIGKILL', UNIT)
    restart()
    return {'restarted': True}


def disarm(directory: Path) -> dict[str, bool]:
    if not (directory.parent / 'active').exists() and not (directory.parent / 'active').is_symlink():
        if OVERRIDE.exists() or OVERRIDE.is_symlink():
            raise RuntimeError('Controller override has no ownership record; refusing cleanup')
        return {'disarmed': True}
    path = active(directory)
    with locked(STATE / 'run.lock'):
        if OVERRIDE.is_symlink() or (OVERRIDE.exists() and OVERRIDE.read_bytes() != TEMPLATE.read_bytes()):
            raise RuntimeError('Controller override changed; refusing cleanup')
        OVERRIDE.unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    restart()
    path.unlink()
    (directory / 'held').unlink(missing_ok=True)
    sync_directory(directory.parent)
    return {'disarmed': True}


def verify_absent(directory: Path) -> dict[str, bool]:
    if (directory / 'files').exists() or (directory / 'files').is_symlink():
        raise RuntimeError('Interrupted extraction published a destination')
    if any(directory.glob('.protondrive-extract-*')):
        raise RuntimeError('Interrupted extraction left staging data')
    return {'absent': True}
