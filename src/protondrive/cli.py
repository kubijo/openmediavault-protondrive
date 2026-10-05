"""Fixed root/admin entry points used by OMV RPC and systemd."""

import argparse
import json
import os
import signal
import subprocess
import sys

from .common import STATE, BackupError, locked, request
from .config import load, validate
from .json_data import JSONValue, decode, object_value
from .models import ServiceStatus
from .recovery import Recovery

UNIT = 'omv-protondrive-backup.service'


def active() -> bool:
    state = subprocess.run(
        ['systemctl', 'show', UNIT, '--property=ActiveState', '--value'], check=True, capture_output=True, text=True
    ).stdout.strip()
    return state in ('active', 'activating', 'deactivating', 'reloading')


def idle() -> None:
    if active():
        raise BackupError('A backup is running; wait for completion or cancel it first')


def follow_run() -> None:
    with locked(STATE / 'admission.lock'):
        idle()
        # systemctl start waits for the oneshot. The job itself survives loss of
        # this observer (including OMV background-task termination).
        follower = subprocess.Popen(
            ['journalctl', '--quiet', '--no-pager', '--follow', '--lines=0', '--output=cat', '--unit=' + UNIT]
        )
        try:
            result = subprocess.run(['systemctl', 'start', UNIT], check=False)
            if result.returncode:
                raise BackupError('Backup failed; see the Proton Drive log')
        finally:
            follower.terminate()
            follower.wait()
        if (STATE / 'status.json').exists():
            print((STATE / 'status.json').read_text(), flush=True)


def recover() -> None:
    with locked(STATE / 'run.lock'):
        Recovery().restore()


class Arguments(argparse.Namespace):
    command: str = ''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'command',
        choices=[
            'daemon',
            'run',
            'run-now',
            'recover',
            'status',
            'auth-status',
            'start-auth',
            'cancel-auth',
            'logout',
            'cancel-run',
            'check-idle',
            'validate',
            'validate-stdin',
        ],
    )
    args = parser.parse_args(namespace=Arguments())
    if args.command != 'daemon' and os.geteuid() != 0 and args.command not in ('validate', 'validate-stdin'):
        raise BackupError('This operation requires root')
    if args.command == 'daemon':
        from .daemon import serve

        serve()
    elif args.command == 'run':
        from .runner import run

        run()
    elif args.command == 'run-now':
        follow_run()
    elif args.command == 'recover':
        recover()
    elif args.command == 'check-idle':
        idle()
    elif args.command == 'validate':
        load()
    elif args.command == 'validate-stdin':
        validate(decode(sys.stdin.read()))
    elif args.command == 'cancel-run':
        subprocess.run(['systemctl', 'stop', UNIT], check=True)
        recover()
        print('true')
    elif args.command in ('status', 'auth-status'):
        try:
            auth: ServiceStatus = request('status')
        except (BackupError, OSError, ValueError, TypeError):
            auth = {
                'state': 'unavailable',
                'url': '',
                'error': 'Apply the plugin configuration to start the Proton service',
                'email': '',
                'organization': '',
                'transferphase': '',
                'transferfile': '',
                'transferelapsed': 0,
            }
        value: dict[str, JSONValue] = {
            'authstate': auth['state'],
            'authurl': auth['url'],
            'autherror': auth['error'],
            'accountemail': auth.get('email', ''),
            'accountorganization': auth.get('organization', ''),
        }
        if args.command == 'status':
            value.update(phase='idle', lastsuccess='', error='', sets={})
            if (STATE / 'status.json').exists():
                value.update(object_value(decode((STATE / 'status.json').read_text())))
            value['running'] = active()
            value.update(
                transferphase=auth.get('transferphase', '') if value['running'] else '',
                transferfile=auth.get('transferfile', '') if value['running'] else '',
                transferelapsed=auth.get('transferelapsed', 0) if value['running'] else 0,
            )
            value['details'] = json.dumps(value.get('sets', {}), indent=2)
        print(json.dumps(value))
    else:
        idle()
        print(json.dumps(request(args.command)))


if __name__ == '__main__':
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        main()
    except (BackupError, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
