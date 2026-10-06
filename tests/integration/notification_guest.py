"""Exercise notification registration, upgrade and removal in a disposable OMV guest."""

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

if __package__:
    from .guest_support import decode_value, items, mapping, run, string
else:
    from guest_support import decode_value, items, mapping, run, string

STATE = Path('/var/lib/openmediavault-protondrive')
MONIT_CONFIG = Path('/etc/monit/conf.d/openmediavault-protondrive.conf')


def rpc(service: str, method: str, params: dict[str, object] | None = None) -> object:
    result = run('omv-rpc', '-u', 'admin', service, method, json.dumps(params or {}), capture_output=True, text=True)
    return decode_value(result.stdout)


def events() -> dict[str, dict[str, object]]:
    return {
        string(event['id']): event
        for raw in items(rpc('Notification', 'getList'))
        if (event := mapping(raw)) and string(event['id']).startswith('protondrive')
    }


def wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 90
    while not predicate():
        if time.monotonic() >= deadline:
            run('monit', '-B', '-g', 'protondrive', 'status', check=False)
            raise AssertionError('Monit did not apply the expected lifecycle state')
        time.sleep(1)


def monitoring(enabled: bool) -> bool:
    result = run('monit', '-B', 'status', 'protondrive-service', check=False, capture_output=True, text=True)
    expected = ['monitoring', 'status', *(['Monitored'] if enabled else ['Not', 'monitored'])]
    return result.returncode == 0 and any(line.split() == expected for line in result.stdout.splitlines())


def main(package: Path) -> None:
    if not Path('/run/protondrive-disposable-test').exists():
        raise SystemExit('Requires a disposable VM')
    if mapping(rpc('EmailNotification', 'get'))['enable']:
        raise RuntimeError('Notification lifecycle tests require external email delivery to be disabled')
    initial = events()
    assert set(initial) == {'protondrive' + name for name in ('backup', 'recovery', 'auth', 'service')}
    assert len({string(event['uuid']) for event in initial.values()}) == 4
    assert all(event['enable'] is False for event in initial.values())
    chosen = initial['protondrivebackup']
    rpc('Notification', 'set', {key: chosen[key] for key in ('id', 'uuid')} | {'enable': True})
    expected = events()
    run('omv-salt', 'deploy', 'run', 'protondrive', 'monit')
    wait_for(lambda: monitoring(True))

    run('dpkg', '-i', str(package))
    assert events() == expected, 'Package upgrade changed notification choices or UUIDs'
    wait_for(lambda: monitoring(False))
    run('omv-salt', 'deploy', 'run', 'protondrive', 'monit')
    wait_for(lambda: monitoring(True))
    run('monit', '-t')
    run('monit', '-g', 'protondrive', 'unmonitor')
    wait_for(lambda: monitoring(False))
    run('/var/lib/dpkg/info/openmediavault-protondrive.postinst', 'abort-upgrade')
    wait_for(lambda: monitoring(True))
    print('PASS: distinct opt-in notification events and preferences retained across upgrade', flush=True)

    recovery = STATE / 'recovery.json'
    assert not recovery.exists() and not recovery.is_symlink()
    recovery.symlink_to(STATE / 'missing-recovery-evidence')
    try:
        result = run('dpkg', '--remove', 'openmediavault-protondrive', check=False, capture_output=True, text=True)
        assert result.returncode != 0 and 'symlink' in result.stderr + result.stdout
        assert recovery.is_symlink(), 'Failed removal discarded recovery evidence'
        assert monitoring(True), 'Failed recovery disabled monitoring'
    finally:
        recovery.unlink()

    evidence = STATE / 'notification-lifecycle-evidence'
    evidence.write_text('Disposable retention assertion\n')

    def removed() -> bool:
        result = run('monit', '-B', 'status', check=False, capture_output=True, text=True)
        return (
            result.returncode == 0
            and result.stdout.startswith('Monit ')
            and all(
                f"Program 'protondrive-{name}'" not in result.stdout
                for name in ('backup', 'recovery', 'auth', 'service')
            )
        )

    # Reproduce a failed deployment: the file exists, but the running daemon
    # has not registered its checks. Removal must not require registration.
    configuration = MONIT_CONFIG.read_bytes()
    MONIT_CONFIG.unlink()
    run('monit', 'reload')
    wait_for(removed)
    MONIT_CONFIG.write_bytes(configuration)
    MONIT_CONFIG.chmod(0o600)
    run('dpkg', '--remove', 'openmediavault-protondrive')
    assert not MONIT_CONFIG.exists()

    wait_for(removed)
    run('monit', '-t')
    assert evidence.read_text() == 'Disposable retention assertion\n'
    run('dpkg', '--purge', 'openmediavault-protondrive')
    assert evidence.exists(), 'Purge removed backup/recovery evidence'
    assert not Path('/etc/openmediavault/protondrive.json').exists()
    print('PASS: failed recovery blocks removal; clean removal unregisters monitoring and retains evidence', flush=True)


if __name__ == '__main__':
    main(Path(sys.argv[1]))
