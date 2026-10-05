#!/usr/bin/env python3
"""Subprocess fixture for the CLI login protocol; configured by a JSON sidecar."""

import json
import sys
import time
from pathlib import Path
from typing import cast


def main():
    match sys.argv[1:3]:
        case ['auth', 'login']:
            delay = cast(object, json.loads(Path(__file__).with_suffix('.json').read_text())['delay'])
            if not isinstance(delay, (int, float)):
                raise TypeError('Expected numeric delay')
            print('Open following URL manually:', flush=True)
            print('https://account.proton.me/desktop/login?app=drive#payload=TEST', flush=True)
            time.sleep(delay)
        case ['filesystem', 'info']:
            print(json.dumps({'uid': 'root', 'ownedBy': {'email': 'test@example.org', 'organization': 'Test team'}}))
        case ['auth', 'logout']:
            pass
        case _:
            sys.exit('Unsupported fixture command')


if __name__ == '__main__':
    main()
