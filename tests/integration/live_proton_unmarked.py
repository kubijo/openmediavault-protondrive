"""Exercise refusal of nonempty unmarked storage in a disposable remote instance."""

import argparse
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from protondrive.common import BackupError
from protondrive.config import load
from protondrive.protoncli import ProtonCli, node_result

RUNTIME = Path('/run/omv-protondrive')
DEVELOPMENT_ROOT = '/my-files/open-media-vault-proton-backup-development'


def service_pid() -> int:
    result = subprocess.run(
        ['pgrep', '-u', 'protondrive', '-f', '^/usr/bin/python3 /usr/sbin/omv-protondrive daemon$'],
        check=True,
        capture_output=True,
        text=True,
    )
    pids = result.stdout.split()
    if len(pids) != 1:
        raise RuntimeError('Expected exactly one Proton service process')
    return int(pids[0])


def service_environment(pid: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for entry in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
        if b'=' not in entry:
            continue
        key, value = entry.split(b'=', 1)
        values[os.fsdecode(key)] = os.fsdecode(value)
    if 'DBUS_SESSION_BUS_ADDRESS' not in values:
        raise RuntimeError('Proton service has no private D-Bus session')
    values['PYTHONPATH'] = '/usr/share/openmediavault-protondrive'
    return values


class FixtureCli(ProtonCli):
    def trash_fixture(self, folder: str, uid: str) -> None:
        node_result(self._run(['filesystem', 'trash', folder]), uid)


def inside() -> None:
    config = load()
    if config['remotepath'] != DEVELOPMENT_ROOT:
        raise RuntimeError('Unmarked-folder test requires the dedicated development root')
    cli = FixtureCli(config, owner_id='disposable-test-owner')
    existing = {entry['name'] for entry in cli.list(DEVELOPMENT_ROOT)}
    name = str(uuid.uuid4())
    while name in existing:
        name = str(uuid.uuid4())
    config['instanceuuid'] = name
    folder = DEVELOPMENT_ROOT + '/' + name
    created = False
    try:
        cli.ensure_folder(folder)
        created = True
        with tempfile.TemporaryDirectory(prefix='live-unmarked-', dir=RUNTIME) as temporary:
            fixture = Path(temporary) / 'unmarked-test.txt'
            fixture.write_text('Disposable collision fixture.\n')
            cli.upload(fixture, folder)
        listing = cli.list(folder)
        if len(listing) != 1 or listing[0]['name'] != 'unmarked-test.txt':
            raise RuntimeError('Disposable remote folder did not contain exactly the test file')
        try:
            cli.ensure_instance_owned()
        except BackupError as error:
            if 'no owner marker' not in str(error):
                raise
        else:
            raise RuntimeError('Nonempty unmarked folder was silently claimed')
        after = cli.list(folder)
        if len(after) != 1 or after[0]['uid'] != listing[0]['uid']:
            raise RuntimeError('Rejected claim modified the disposable remote folder')
        print('PASS: nonempty unmarked instance refused without changing its contents')
    finally:
        if created:
            matches = [entry for entry in cli.list(DEVELOPMENT_ROOT) if entry['name'] == name]
            if len(matches) != 1 or matches[0]['type'] != 'folder':
                raise RuntimeError('Disposable instance folder became ambiguous; cleanup stopped')
            cli.trash_fixture(folder, matches[0]['uid'])


def outside() -> None:
    if not Path('/var/lib/protondrive-interactive-vm').exists():
        raise RuntimeError('This test only runs inside the dedicated interactive VM')
    script = Path(__file__).resolve()
    if script.parent != Path('/usr/local/lib/omv-protondrive-vm').resolve(strict=True):
        raise RuntimeError('Run the built VM helper from /usr/local/lib/omv-protondrive-vm')
    pid = service_pid()
    command = [
        'nsenter',
        '-t',
        str(pid),
        '-m',
        '--',
        'setpriv',
        '--reuid=protondrive',
        '--regid=protondrive',
        '--init-groups',
        'python3',
        str(script),
        '--inside',
    ]
    result = subprocess.run(
        command,
        env=service_environment(pid),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f'Unmarked-folder test failed with exit {result.returncode}')
    print(result.stdout, end='')


class Arguments(argparse.Namespace):
    inside: bool


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--inside', action='store_true')
    args = parser.parse_args(namespace=Arguments())
    if args.inside:
        inside()
    else:
        outside()


if __name__ == '__main__':
    main()
