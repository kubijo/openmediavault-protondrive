"""Verify the real Docker/systemd recovery boundary in the disposable OMV guest."""

import io
import shutil
import subprocess
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

if __package__:
    from .guest_support import decode, items, run
else:
    from guest_support import decode, items, run

STATE = Path('/var/lib/openmediavault-protondrive')
BACKUP = 'omv-protondrive-backup.service'


def running(name: str) -> bool:
    return (
        run('docker', 'inspect', '--format', '{{.State.Running}}', name, capture_output=True, text=True).stdout.strip()
        == 'true'
    )


def wait_for(predicate: Callable[[], bool], message: str) -> None:
    deadline = time.monotonic() + 45
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(message)
        time.sleep(0.1)


def fixture_image():
    with io.BytesIO() as data:
        with tarfile.open(fileobj=data, mode='w') as archive:
            archive.add('/bin/busybox', arcname='busybox')
        run('docker', 'import', '-', 'protondrive-fixture', input=data.getvalue())
    for name in ('originally-running', 'originally-stopped'):
        run('docker', 'create', '--name', name, 'protondrive-fixture', '/busybox', 'sleep', '86400')
    run('docker', 'start', 'originally-running')


def main():
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Requires the disposable VM')
    fixture_image()
    override = Path(f'/run/systemd/system/{BACKUP}.d')
    override.mkdir(parents=True)
    # Only replace the workload, retaining the shipped stop/recovery semantics.
    shutil.copyfile('/src/tests/fixtures/recovery.service.conf', override / 'test.conf')
    run('systemctl', 'daemon-reload')
    for signum in ('SIGTERM', 'SIGKILL'):
        marker = Path('/run/protondrive-stop-complete')
        marker.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'reset-failed', BACKUP], check=False)
        run('systemctl', 'start', '--no-block', BACKUP)
        wait_for(marker.exists, 'The fixture failed to stop containers')
        assert not running('originally-running')
        assert not running('originally-stopped')
        record = decode((STATE / 'recovery.json').read_text())
        assert len(items(record['ids'])) == 1
        run('systemctl', 'kill', '--kill-whom=main', f'--signal={signum}', BACKUP)
        wait_for(lambda: not (STATE / 'recovery.json').exists(), 'Recovery record was not cleared')
        assert running('originally-running')
        assert not running('originally-stopped')
        print(f'PASS: {signum} restores only previously running containers', flush=True)
    (override / 'test.conf').unlink()
    run('systemctl', 'daemon-reload')
    run('docker', 'rm', '--force', 'originally-running', 'originally-stopped')


if __name__ == '__main__':
    main()
