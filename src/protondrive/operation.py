"""Cooperative cancellation with a single, irreversible publication boundary."""

import threading
from collections.abc import Callable

from .common import BackupError


class Cancelled(BackupError):
    pass


class OperationControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._sealed = False

    def check(self) -> None:
        if self._cancelled.is_set():
            raise Cancelled('Operation cancelled')

    def cancel(self) -> bool:
        with self._lock:
            if self._sealed:
                return False
            self._cancelled.set()
            return True

    def commit(self, action: Callable[[], None]) -> None:
        with self._lock:
            self.check()
            # Once publication starts, cancellation cannot claim that it undid
            # the result. Restart recovery reconciles an uncertain outcome.
            self._sealed = True
            action()
