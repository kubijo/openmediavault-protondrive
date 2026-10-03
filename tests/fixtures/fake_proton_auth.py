#!/usr/bin/env python3
"""Subprocess fixture for the CLI login protocol; configured by a JSON sidecar."""

import json
import sys
import time
from pathlib import Path


def main():
    match sys.argv[1:3]:
        case ['auth', 'login']:
            settings = json.loads(Path(__file__).with_suffix('.json').read_text())
            print('Open following URL manually:', flush=True)
            print('https://account.proton.me/desktop/login?app=drive#payload=TEST', flush=True)
            time.sleep(settings['delay'])
        case ['filesystem', 'info']:
            print(json.dumps({'uid': 'root', 'ownedBy': {'email': 'test@example.org', 'organization': 'Test team'}}))
        case ['auth', 'logout']:
            pass
        case _:
            sys.exit('Unsupported fixture command')


if __name__ == '__main__':
    main()
