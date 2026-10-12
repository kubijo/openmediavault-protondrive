"""Turn terminal termination into an exception so process cleanup can finish."""

import signal
from collections.abc import Generator
from contextlib import contextmanager
from types import FrameType


class TerminationRequested(KeyboardInterrupt):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(signal.Signals(signum).name)


@contextmanager
def termination_signals() -> Generator[None, None, None]:
    watched = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {signum: signal.getsignal(signum) for signum in watched}

    def terminate(signum: int, frame: FrameType | None) -> None:
        # Repeated terminal signals must not interrupt shutdown or remote cancellation.
        for item in watched:
            signal.signal(item, signal.SIG_IGN)
        raise TerminationRequested(signum)

    try:
        for signum in watched:
            signal.signal(signum, terminate)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
