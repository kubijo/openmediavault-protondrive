"""Local QMP control and private persistent VM state, without PID-based signalling."""

import fcntl
import json
import socket
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tool_data import decode, mapping


def qmp(path: Path, command: str) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        with connection.makefile('rwb', buffering=0) as stream:
            line = stream.readline(65536)
            if not line:
                raise ConnectionResetError('QEMU monitor closed')
            greeting = decode(line)
            if 'QMP' not in greeting:
                raise RuntimeError('Invalid QEMU monitor greeting')
            for request in ('qmp_capabilities', command):
                stream.write(json.dumps({'execute': request, 'id': request}).encode() + b'\n')
                while True:
                    line = stream.readline(65536)
                    if not line:
                        raise ConnectionResetError('QEMU monitor closed before acknowledging the command')
                    response = decode(line)
                    if response.get('id') != request:
                        continue
                    if 'error' in response:
                        raise RuntimeError(mapping(response['error']).get('desc', 'QEMU monitor error'))
                    if 'return' in response:
                        break
            return mapping(response['return'])


@dataclass
class VMState:
    root: Path

    @property
    def instance(self) -> Path:
        return self.root / 'instance'

    @property
    def monitor(self) -> Path:
        return self.root / 'control.sock'

    @property
    def metadata(self) -> Path:
        return self.instance / 'instance.json'

    def read(self) -> dict[str, object]:
        if not self.metadata.exists():
            raise RuntimeError('VM has not been created; run just vm::up')
        return decode(self.metadata.read_text())

    def write(self, metadata: Mapping[str, object]) -> None:
        temporary = self.metadata.with_suffix('.new')
        temporary.write_text(json.dumps(metadata, indent=2) + '\n')
        temporary.replace(self.metadata)

    def running(self) -> dict[str, object] | None:
        try:
            return qmp(self.monitor, 'query-status')
        except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError):
            return None

    @contextmanager
    def lock(self, name: str = 'lifecycle') -> Generator[None, None, None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        with (self.root / f'{name}.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(
                    f'VM {name} operation already active; stop it before resetting or restarting'
                ) from None
            yield

    def stop(
        self, *, force: bool = False, timeout: float = 120, request_shutdown: Callable[[], None] | None = None
    ) -> None:
        if self.running() is None:
            return
        try:
            if request_shutdown is not None and not force:
                request_shutdown()
            else:
                qmp(self.monitor, 'quit' if force else 'system_powerdown')
        except (ConnectionResetError, ConnectionRefusedError, FileNotFoundError):
            pass
        deadline = time.monotonic() + timeout
        while self.running() is not None:
            if time.monotonic() >= deadline:
                raise RuntimeError('VM did not shut down; it is still running. Use vm::down --force only if needed')
            time.sleep(0.2)
