"""Subprocess behaviours used by the terminal streaming tests."""

import os
import sys
import time
from pathlib import Path

mode = sys.argv[1]

if mode == 'stream':
    os.write(1, b'[red]literal\n')
    deadline = time.monotonic() + 5
    while not Path(sys.argv[2]).exists():
        if time.monotonic() > deadline:
            raise SystemExit('Output was not relayed while the process was running')
        time.sleep(0.01)
    os.write(2, b'stderr\n')
    os.write(1, b'\xe2')
    time.sleep(0.2)
    os.write(1, b'\x82\xac\nfinal without newline')
elif mode == 'failure':
    print('diagnostic before failure', flush=True)
    raise SystemExit(7)
elif mode == 'timeout':
    Path(sys.argv[2]).write_text(str(os.getpid()))
    print('ready', flush=True)
    time.sleep(30)
elif mode == 'environment':
    assert not sys.stdin.read()
    assert os.environ['NO_COLOR'] == '1'
    assert os.environ['TERM'] == 'dumb'
    print('plain child with stdin closed')
elif mode == 'colour':
    assert not sys.stdin.read()
    assert 'NO_COLOR' not in os.environ
    assert os.environ['TERM'] == 'xterm-256color'
    os.write(1, b'\x1b[32mworking\r')
    time.sleep(0.2)
    os.write(1, b'complete\x1b[0m\r\n')
elif mode == 'remote-environment':
    assert not sys.stdin.read()
    assert not sys.stdin.isatty()
    assert sys.stdout.isatty()
    assert os.environ['TERM'] == 'xterm-256color'
    assert os.environ['FORCE_COLOR'] == '1'
    print('guest output terminal with input disconnected')
else:
    raise SystemExit(f'Unknown fixture mode: {mode}')
