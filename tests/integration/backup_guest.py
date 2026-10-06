"""Exercise the installed runner/daemon with real archives and a fake remote backend."""

import os
import shutil
import subprocess
import time
from pathlib import Path

if __package__:
    from .guest_support import decode, integer, items, mapping, run
else:
    from guest_support import decode, integer, items, mapping, run

from omv_guest import rpc

STATE = Path('/var/lib/openmediavault-protondrive')
REMOTE = STATE / 'proton/fake-remote/my-files'
BINARY = Path('/usr/lib/openmediavault-protondrive/proton-drive')
BACKUP = 'omv-protondrive-backup.service'


def configure():
    data = Path('/data/fixture')
    data.mkdir(parents=True)
    (data / 'database').mkdir()
    (data / 'database/content').write_text('backup fixture\n')
    os.chown(data / 'database', 1234, 2345)
    (data / 'database').chmod(0o2750)
    os.setxattr(data / 'database/content', 'user.backup-test', b'preserved')
    (data / 'cache').mkdir()
    (data / 'cache/ignored').touch()
    sets = rpc('getSetList', {'start': 0, 'limit': -1, 'sortfield': 'name', 'sortdir': 'ASC'})['data']
    for raw in items(sets):
        item = mapping(raw)
        item['enable'] = item['name'] == 'appData'
        if item['enable']:
            item.update(paths=str(data), excludes='cache', localkeep=1, remotekeep=1)
        rpc('setSet', item)
    run('omv-salt', 'deploy', 'run', 'protondrive')


def run_backup():
    completion = STATE / 'backup-completion.json'
    previous = integer(decode(completion.read_text())['generation']) if completion.exists() else 0
    subprocess.run(['systemctl', 'reset-failed', BACKUP], check=False)
    run('systemctl', 'start', BACKUP)
    status = decode((STATE / 'status.json').read_text())
    assert status['phase'] == 'completed'
    recorded = decode(completion.read_text())
    assert recorded['generation'] == previous + 1
    assert recorded['timestamp'] == status['lastsuccess']


def main():
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Requires the disposable VM')
    configure()
    original = BINARY.with_suffix('.vendor')
    BINARY.rename(original)
    try:
        shutil.copyfile('/src/tests/fixtures/fake_proton_filesystem.py', BINARY)
        BINARY.chmod(0o755)
        run('systemctl', 'restart', 'omv-protondrive')
        deadline = time.monotonic() + 20
        while not Path('/run/omv-protondrive/control.sock').exists():
            if time.monotonic() > deadline:
                raise AssertionError('Daemon socket did not start')
            time.sleep(0.1)
        run_backup()
        archives = list(REMOTE.rglob('*.tar.zst'))
        assert len(archives) == 1
        restore = Path('/data/restored')
        restore.mkdir()
        run(
            'tar',
            '--extract',
            '--zstd',
            '--numeric-owner',
            '--same-owner',
            '--same-permissions',
            '--acls',
            '--xattrs',
            '--file',
            str(archives[0]),
            '--directory',
            str(restore),
        )
        database = restore / 'data/fixture/database'
        assert (database.stat().st_uid, database.stat().st_gid, database.stat().st_mode & 0o7777) == (
            1234,
            2345,
            0o2750,
        )
        assert (database / 'content').read_text() == 'backup fixture\n'
        assert os.getxattr(database / 'content', 'user.backup-test') == b'preserved'
        assert not (restore / 'data/fixture/cache').exists()
        print('PASS: installed runner/daemon backup, remote pair, metadata restore, exclusions', flush=True)
    finally:
        run('systemctl', 'stop', 'omv-protondrive')
        BINARY.unlink(missing_ok=True)
        original.rename(BINARY)


if __name__ == '__main__':
    main()
