"""Hold a real container-stop interval open for VM service termination tests."""

import signal
from pathlib import Path

from protondrive.recovery import Recovery


def main():
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Requires the disposable VM')
    Recovery().stop(2)
    Path('/run/protondrive-stop-complete').touch()
    signal.pause()


if __name__ == '__main__':
    main()
