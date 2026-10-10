"""Deterministic program status for the isolated Monit delivery test."""

import sys
from pathlib import Path

from tool_data import decode


def main() -> int:
    state = decode(Path(sys.argv[1]).read_text())
    failed = state[sys.argv[2]]
    if not isinstance(failed, bool):
        raise TypeError('Fixture health must be boolean')
    print('Fixture incident' if failed else 'Fixture healthy', file=sys.stderr if failed else sys.stdout)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
