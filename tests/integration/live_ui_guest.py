"""Guard, prepare, verify, and clean up the authenticated browser flow in the test VM."""

import argparse
import fcntl
import io
import json
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Protocol

from protondrive.cli import active, idle
from protondrive.common import CONFIG, STATE, atomic_json, request
from protondrive.config import load
from protondrive.json_data import decode, object_value
from protondrive.models import Configuration, RemoteEntry

if __package__:
    from .flow_records import BackupStatus, FlowRecord, backup_status, flow_record
    from .live_proton_guest import verify_backups
else:
    from flow_records import BackupStatus, FlowRecord, backup_status, flow_record
    from live_proton_guest import verify_backups

FLOW = Path('/var/lib/protondrive-ui-flow')
ROOT = '/my-files/open-media-vault-proton-backup-development'
UNIT = 'omv-protondrive-backup.service'
OVERRIDE = Path(f'/run/systemd/system/{UNIT}.d/90-ui-flow.conf')
IMAGE = 'protondrive-ui-flow:local'
LABEL = 'omv-protondrive-ui-flow'
LOCK = Path('/run/lock/protondrive-ui-flow.lock')


def lease() -> dict[str, bool]:
    """Hold the VM-wide flow lock until the browser controller disconnects."""
    with LOCK.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another live UI flow is running') from error
        print(json.dumps({'locked': True}), flush=True)
        sys.stdin.read()
    return {'released': True}


def run(*args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, check=True, capture_output=True, timeout=120, input=input)


def status() -> BackupStatus:
    path = STATE / 'status.json'
    return backup_status(decode(path.read_text()) if path.exists() else {})


def guard(config: Configuration) -> None:
    if not Path('/var/lib/protondrive-interactive-vm').is_file():
        raise RuntimeError('Requires the interactive test VM')
    if config['remotepath'] != ROOT or config['enable']:
        raise RuntimeError('Requires the development root and disabled scheduling')
    if {item['name'] for item in config['sets']} != {'system', 'appData'} or len(config['sets']) != 2:
        raise RuntimeError('Requires exactly the two interactive fixture sets')
    for item in config['sets']:
        if not item['enable'] or item['paths'] != f'/data/interactive-fixtures/{item["name"]}':
            raise RuntimeError('Refusing non-fixture backup sources')
        if not (Path(item['paths']) / 'example.txt').is_file():
            raise RuntimeError('Missing interactive fixture content')


def wait_for_new_minute(config: Configuration) -> None:
    # Archive names have minute resolution. Do not mistake a naming collision
    # with the preceding successful run for a cancellation test.
    deadline = time.monotonic() + 65
    while True:
        stamp = datetime.now(UTC).strftime('%Y%m%dT%H%MZ')
        if not any(
            (Path(config['stagingpath']) / item['uuid'] / f'{item["name"]}-{stamp}.tar.zst').exists()
            for item in config['sets']
        ):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Archive timestamp remained occupied')
        time.sleep(1)


def begin() -> dict[str, bool | str]:
    config = load()
    guard(config)
    if FLOW.exists():
        raise RuntimeError('Previous UI flow state exists; choose recovery before starting a new flow')
    idle()
    if (STATE / 'recovery.json').exists() or OVERRIDE.exists():
        raise RuntimeError('Existing recovery or UI test override needs inspection')
    if run('docker', 'ps', '-aq').stdout.strip():
        raise RuntimeError('Cancellation test requires a VM without existing containers')
    if run('docker', 'image', 'ls', '--quiet', IMAGE).stdout.strip():
        raise RuntimeError('Existing UI test image needs inspection')
    if request('probe')['state'] != 'signed-in':
        raise RuntimeError('Sign into the dedicated Proton account before running the live flow')
    wait_for_new_minute(config)
    FLOW.mkdir(mode=0o700)
    try:
        shutil.copy2(CONFIG, FLOW / 'config.json')
        atomic_json(FLOW / 'record.json', {'before': status(), 'containers': []})
    except BaseException:
        shutil.rmtree(FLOW)
        raise
    return {'ready': True, 'lastsuccess': status().get('lastsuccess', '')}


def inspect() -> dict[str, bool | str]:
    return {'pending': FLOW.exists(), 'path': str(FLOW)}


def record() -> FlowRecord:
    return flow_record(decode((FLOW / 'record.json').read_text()))


def remote(config: Configuration) -> dict[str, list[RemoteEntry]]:
    return {
        item['uuid']: sorted(request('prepare', setuuid=item['uuid']), key=lambda entry: entry['uid'])
        for item in config['sets']
    }


def report_activity(phase: str, name: str) -> None:
    print(json.dumps({'event': 'activity', 'phase': phase, 'file': name}), flush=True)


def verify() -> dict[str, object]:
    idle()
    current = status()
    if current.get('phase') != 'completed' or current.get('lastsuccess') == record()['before'].get('lastsuccess'):
        raise RuntimeError('The browser did not complete a new backup')
    restored = verify_backups(report_activity)
    for item in restored:
        if current['sets'][item['set']]['archive'] != item['archive']:
            raise RuntimeError('Restore selected an archive from another run')
    return {'restored': restored, 'lastsuccess': current['lastsuccess']}


def prepare_cancel() -> dict[str, bool]:
    idle()
    config = load()
    guard(config)
    wait_for_new_minute(config)
    value = record()
    value.update(remote=remote(config), completed=status())
    atomic_json(FLOW / 'record.json', value)
    prepare_containers(config, value)
    OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name('ui-cancel.conf'), OVERRIDE)
    run('systemctl', 'daemon-reload')
    return {'armed': True}


def prepare_containers(config: Configuration, value: FlowRecord) -> None:
    # Import a local static busybox; no image registry or application data is used.
    with io.BytesIO() as stream:
        with tarfile.open(fileobj=stream, mode='w') as archive:
            archive.add('/bin/busybox', arcname='busybox')
        run('docker', 'import', '-', IMAGE, input=stream.getvalue())
    for name in ('running', 'stopped'):
        cid = (
            run(
                'docker',
                'create',
                '--label',
                LABEL,
                '--name',
                f'protondrive-ui-{name}',
                IMAGE,
                '/busybox',
                'sleep',
                '86400',
            )
            .stdout.decode()
            .strip()
        )
        value['containers'].append(cid)
        atomic_json(FLOW / 'record.json', value)
    run('docker', 'start', value['containers'][0])
    for item in config['sets']:
        item['stopcontainers'] = True
    config['containerstoptimeout'] = 1
    atomic_json(FLOW / 'fixture-config.json', config)
    atomic_json(CONFIG, config)


def running(cid: str) -> bool:
    return run('docker', 'inspect', '--format', '{{.State.Running}}', cid).stdout.strip() == b'true'


def held() -> dict[str, bool | str]:
    if not (FLOW / 'archiving').exists():
        return {'held': False, 'phase': status().get('phase')}
    containers = record()['containers']
    recovery = object_value(decode((STATE / 'recovery.json').read_text()))
    if recovery['ids'] != [containers[0]] or any(running(cid) for cid in containers):
        raise RuntimeError('The real runner did not stop only the running fixture container')
    if not active():
        raise RuntimeError('Backup exited before the UI cancellation')
    return {'held': True}


def recovered() -> dict[str, bool]:
    idle()
    value = record()
    current = status()
    if current.get('phase') != 'failed' or 'cancelled by signal' not in current.get('error', ''):
        raise RuntimeError('Expected a cancelled backup, not an unrelated failure')
    if current.get('lastsuccess') != value['completed']['lastsuccess']:
        raise RuntimeError('Cancellation changed the last successful backup')
    if (STATE / 'recovery.json').exists():
        raise RuntimeError('Container recovery is incomplete')
    if not running(value['containers'][0]) or running(value['containers'][1]):
        raise RuntimeError('Cancellation did not restore the original container states')
    config = load()
    if remote(config) != value['remote']:
        raise RuntimeError('Cancelled archive changed remote backup contents')
    if list(Path(config['stagingpath']).glob('*/*.partial')):
        raise RuntimeError('Cancellation left partial archives')
    return {'cancelled': True, 'running_container_restored': True, 'stopped_container_preserved': True}


def cleanup() -> dict[str, bool]:
    if not FLOW.exists():
        return {'cleaned': True}
    retry = (FLOW / 'retry.json').exists()
    if retry:
        retry_module().disarm()
    # An interrupted initialization cannot have created any external fixtures.
    # Refuse partial records alongside evidence of later mutation.
    if not (FLOW / 'record.json').exists():
        if (
            set(FLOW.iterdir()) <= {FLOW / 'config.json'}
            and not OVERRIDE.exists()
            and not (STATE / 'recovery.json').exists()
        ):
            idle()
            shutil.rmtree(FLOW)
            return {'cleaned': True}
        raise RuntimeError('Incomplete UI flow record needs inspection')
    if active():
        run('omv-protondrive', 'cancel-run')
    if (STATE / 'recovery.json').exists():
        run('omv-protondrive', 'recover')
    saved = FLOW / 'config.json'
    fixture = FLOW / 'fixture-config.json'
    if fixture.exists():
        if decode(CONFIG.read_text()) not in (decode(fixture.read_text()), decode(saved.read_text())):
            raise RuntimeError('Configuration changed during the flow; refusing to overwrite it')
        shutil.copy2(saved, CONFIG)
    OVERRIDE.unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    remaining = run('docker', 'ps', '-aq', '--no-trunc', '--filter', f'label={LABEL}').stdout.decode().split()
    for cid in record()['containers']:
        if cid in remaining:
            run('docker', 'rm', '--force', cid)
    if run('docker', 'image', 'ls', '--quiet', IMAGE).stdout.strip():
        run('docker', 'image', 'rm', IMAGE)
    if retry:
        retry_module().remove_fixture()
        run('systemctl', 'restart', 'omv-protondrive')
    shutil.rmtree(FLOW)
    return {'cleaned': True}


class RetryHelper(Protocol):
    def prepare(self) -> object: ...
    def failed(self) -> object: ...
    def verify(self) -> object: ...
    def disarm(self) -> None: ...
    def remove_fixture(self) -> None: ...


def retry_module() -> RetryHelper:
    if __package__:
        from . import live_upload_retry
    else:
        import live_upload_retry
    return live_upload_retry


def interrupted(_signal: int, _frame: FrameType | None) -> None:
    # systemd stops the entire helper process group. Unwind temporary-directory
    # contexts too, so an interrupted download does not leave private scratch data.
    raise KeyboardInterrupt('Live UI operation cancelled')


class Arguments(argparse.Namespace):
    action: str = ''


def main() -> None:
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    actions: dict[str, Callable[[], object]] = {
        'lease': lease,
        'inspect': inspect,
        'begin': begin,
        'verify': verify,
        'prepare-cancel': prepare_cancel,
        'held': held,
        'recovered': recovered,
        'cleanup': cleanup,
        'prepare-retry': lambda: retry_module().prepare(),
        'retry-failed': lambda: retry_module().failed(),
        'verify-retry': lambda: retry_module().verify(),
    }
    parser.add_argument('action', choices=actions)
    action = parser.parse_args(namespace=Arguments()).action
    guard(load())
    print(json.dumps(actions[action]()))


if __name__ == '__main__':
    from console import install_traceback

    install_traceback()
    main()
