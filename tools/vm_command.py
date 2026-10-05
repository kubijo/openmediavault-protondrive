"""Run a command or a structured service request in the persistent test VM."""

import argparse
import json
import subprocess
from pathlib import Path

import vm_runtime
from tool_data import decode, integer
from vm_control import VMState

SOCKET = '/run/omv-protondrive/control.sock'
RESPONSE_LIMIT = 4 * 1024 * 1024


def guest_for(state_dir: Path) -> vm_runtime.Guest:
    state = VMState(state_dir.resolve())
    metadata = state.read()
    if state.running() is None:
        raise RuntimeError('VM is stopped; run just vm::up first')
    return vm_runtime.guest_connection(state.instance, state.instance / 'key', integer(metadata['ssh_port']))


def execute(guest: vm_runtime.Guest, command: list[str], *, stdin: bool = False) -> int:
    while command and command[0] == '--':
        command = command[1:]
    if not command:
        raise ValueError('Supply a guest command after exec --')
    result = guest.run(*command, stdin=None if stdin else subprocess.DEVNULL, check=False)
    return result.returncode


def rpc(guest: vm_runtime.Guest, operation: str, set_uuid: str | None = None) -> object:
    if operation == 'prepare' and not set_uuid:
        raise ValueError('prepare requires --set-uuid')
    if operation != 'prepare' and set_uuid:
        raise ValueError('--set-uuid is only valid for prepare')
    request = {'operation': operation}
    if set_uuid:
        request['setuuid'] = set_uuid
    result = guest.run(
        'socat',
        'STDIO,ignoreeof',
        f'UNIX-CONNECT:{SOCKET}',
        input=json.dumps(request) + '\n',
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f'Guest socket transport failed (exit {result.returncode})')
    if len(result.stdout) > RESPONSE_LIMIT or not result.stdout:
        raise RuntimeError('Missing or oversized service response')
    try:
        response = decode(result.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError('Invalid service response') from error
    if response.get('ok') is not True:
        message = response.get('error', 'Unknown service failure')
        raise RuntimeError(f'Proton service: {message}')
    return response.get('result')


class Arguments(argparse.Namespace):
    state_dir: Path = Path('.tmp/interactive-vm')
    action: str = ''
    command: list[str]
    stdin: bool = False
    operation: str = ''
    set_uuid: str | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('.tmp/interactive-vm'))
    commands = parser.add_subparsers(dest='action', required=True)
    execute_parser = commands.add_parser('exec', help='Run guest argv without a terminal')
    execute_parser.add_argument('--stdin', action='store_true', help='Forward standard input to the guest command')
    execute_parser.add_argument('command', nargs=argparse.REMAINDER)
    request = commands.add_parser('rpc', help='Call the Proton service socket and print its JSON result')
    request.add_argument('operation', choices=('status', 'probe', 'prepare'))
    request.add_argument('--set-uuid')
    options = parser.parse_args(argv, namespace=Arguments())
    guest = guest_for(options.state_dir)
    if options.action == 'exec':
        return execute(guest, options.command, stdin=options.stdin)
    print(json.dumps(rpc(guest, options.operation, options.set_uuid), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
