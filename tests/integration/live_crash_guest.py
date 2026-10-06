"""Crash the real runner and assert durable recovery in the development VM."""

import grp
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import NotRequired, TypedDict

from protondrive.archive import digest
from protondrive.common import STATE, atomic_json, sync_directory
from protondrive.config import load
from protondrive.json_data import decode, object_value, string
from protondrive.retention import metadata
from protondrive.runner import receipt

if __package__:
    from . import live_ui_guest as flow
else:
    import live_ui_guest as flow

RECOVER = 'omv-protondrive-recover.service'
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')


class CrashRecord(TypedDict):
    token: str
    boot_id: str
    mode: str
    preserved: dict[str, str]
    fixtures: dict[str, str]
    workspace: NotRequired[str]


def read_record() -> CrashRecord:
    value = object_value(decode((flow.FLOW / 'crash.json').read_text()))
    mode = string(value['mode'])
    if mode not in ('kill', 'reboot'):
        raise ValueError('Invalid crash recovery mode')
    result: CrashRecord = {
        'token': str(uuid.UUID(string(value['token']))),
        'boot_id': string(value['boot_id']),
        'mode': mode,
        'preserved': {key: string(item) for key, item in object_value(value['preserved']).items()},
        'fixtures': {key: string(item) for key, item in object_value(value['fixtures']).items()},
    }
    if 'workspace' in value:
        result['workspace'] = string(value['workspace'])
    return result


def save(value: CrashRecord) -> None:
    atomic_json(flow.FLOW / 'crash.json', value)


def gate() -> Path:
    return flow.FLOW / 'restart-gate'


def restore_gate() -> None:
    token = read_record()['token']
    path = gate()
    if path.is_symlink() or (path.exists() and path.read_text() != token):
        raise RuntimeError('Restart gate changed; refusing to replace it')
    if not path.exists():
        with path.open('x') as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(path.parent)


def hashes() -> dict[str, str]:
    staging = Path(load()['stagingpath'])
    return {
        str(path): digest(path) for path in staging.glob('*/*') if path.is_file() and not path.name.endswith('.partial')
    }


def verify_hashes(values: dict[str, str]) -> None:
    for name, expected in values.items():
        path = Path(name)
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise RuntimeError(f'Crash changed retained archive data: {name}')


def prepare() -> dict[str, bool]:
    flow.idle()
    config = load()
    flow.guard(config)
    if (flow.FLOW / 'crash.json').exists() or gate().exists():
        raise RuntimeError('Existing crash fixtures need recovery')
    value = flow.record()
    value.update(remote=flow.remote(config), completed=flow.status())
    atomic_json(flow.FLOW / 'record.json', value)
    save(
        {
            'token': str(uuid.uuid4()),
            'boot_id': BOOT_ID.read_text().strip(),
            'mode': 'kill',
            'preserved': hashes(),
            'fixtures': {},
        }
    )
    restore_gate()
    flow.prepare_containers(config, value, restart_gate=gate())
    flow.OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name('ui-cancel.conf'), flow.OVERRIDE)
    flow.run('systemctl', 'daemon-reload')
    flow.wait_for_new_minute(config)
    return {'armed': True}


def checkpoint() -> CrashRecord:
    if flow.held().get('held') is not True:
        raise RuntimeError('Runner has not reached the archive hold')
    value = read_record()
    if value['fixtures'] or 'workspace' in value:
        raise RuntimeError('Previous crash fixtures have not been verified')
    config = load()
    item = next(item for item in config['sets'] if item['name'] == 'system')
    directory = Path(config['stagingpath']) / item['uuid']
    source = next(path for path in sorted(directory.glob('*.tar.zst')) if receipt(path).is_file())
    pending = directory / 'system-19700101T0000Z.tar.zst'
    manifest = pending.with_name(pending.name + '.manifest.json')
    if any(path.exists() or path.is_symlink() for path in (pending, manifest, receipt(pending))):
        raise RuntimeError('Pending crash fixture name is occupied')
    contents = metadata(decode(source.with_name(source.name + '.manifest.json').read_text()), config, item, source.name)
    contents.update(archive=pending.name, timestamp='19700101T0000Z')
    workspace = directory / f'.crash-{value["token"]}'
    if workspace.exists() or workspace.is_symlink():
        raise RuntimeError('Crash workspace name is occupied')
    # Claim the private workspace before creating anything in it. Keep it on
    # the destination filesystem so complete files can be linked without copying.
    value['workspace'] = str(workspace)
    for backup_set in config['sets']:
        for partial in (Path(config['stagingpath']) / backup_set['uuid']).glob('*.partial'):
            value['fixtures'][str(partial)] = digest(partial)
    save(value)
    workspace.mkdir(mode=0o700)
    sync_directory(workspace.parent)
    staged_archive = workspace / pending.name
    staged_manifest = workspace / manifest.name
    # Seed a complete pending pair only after the real runner reaches archiving,
    # so its preflight cannot upload it before the crash.
    with source.open('rb') as src, staged_archive.open('xb') as dst:
        os.fchown(dst.fileno(), 0, grp.getgrnam('protondrive').gr_gid)
        os.fchmod(dst.fileno(), 0o640)
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    atomic_json(staged_manifest, contents, 0o640, owner=(0, grp.getgrnam('protondrive').gr_gid))
    verify_hashes({str(staged_archive): contents['sha256']})
    value['fixtures'][str(pending)] = contents['sha256']
    value['fixtures'][str(manifest)] = digest(staged_manifest)
    save(value)
    # Journal both names before either becomes visible; link refuses replacement.
    os.link(staged_archive, pending)
    os.link(staged_manifest, manifest)
    sync_directory(directory)
    restore_gate()
    gate().unlink()
    sync_directory(gate().parent)
    flow.run('sync')
    return value


def wait_stopped(unit: str) -> None:
    deadline = time.monotonic() + 90
    while flow.run('systemctl', 'show', unit, '--property=ActiveState', '--value').stdout.strip() not in (
        b'failed',
        b'inactive',
    ):
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{unit} did not stop')
        time.sleep(0.2)


def failed() -> dict[str, bool]:
    value = read_record()
    unit = flow.UNIT if value['mode'] == 'kill' else RECOVER
    wait_stopped(unit)
    result = flow.run('systemctl', 'show', unit, '--property=Result', '--value').stdout.strip()
    if result == b'success':
        raise RuntimeError('Expected the blocked container restart to fail')
    ids = flow.record()['containers']
    recovery = object_value(decode((STATE / 'recovery.json').read_text()))
    if recovery.get('ids') != [ids[0]] or any(flow.running(cid) for cid in ids):
        raise RuntimeError('Failed recovery lost its record or changed container states')
    if value['mode'] == 'reboot' and BOOT_ID.read_text().strip() == value['boot_id']:
        raise RuntimeError('The VM did not reboot')
    verify_hashes(value['preserved'])
    verify_hashes(value['fixtures'])
    pending = next(Path(name) for name in value['fixtures'] if name.endswith('.tar.zst'))
    if receipt(pending).exists():
        raise RuntimeError('Pending archive was unexpectedly confirmed')
    return {'recovery_record_retained': True, 'pending_archive_preserved': True, 'containers_stopped': True}


def kill() -> dict[str, bool]:
    checkpoint()
    flow.run('systemctl', 'kill', '--kill-whom=main', '--signal=SIGKILL', flow.UNIT)
    result = failed()
    if flow.run('systemctl', 'show', flow.UNIT, '--property=ExecMainStatus', '--value').stdout.strip() != b'9':
        raise RuntimeError('Runner did not exit from SIGKILL')
    return result


def reboot_ready() -> dict[str, str]:
    if read_record()['mode'] != 'reboot':
        raise RuntimeError('Crash flow is not armed for reboot')
    value = checkpoint()
    return {'boot_id': value['boot_id']}


def remove_fixtures() -> None:
    value = read_record()
    for name, expected in list(value['fixtures'].items()):
        path = Path(name)
        if path.exists() or path.is_symlink():
            verify_hashes({name: expected})
            path.unlink()
            sync_directory(path.parent)
        del value['fixtures'][name]
        save(value)
    if 'workspace' in value:
        workspace = Path(value['workspace'])
        if workspace.is_symlink():
            raise RuntimeError('Crash workspace changed; refusing to remove it')
        if workspace.exists():
            shutil.rmtree(workspace)
            sync_directory(workspace.parent)
        del value['workspace']
        save(value)


def allow_recovery() -> dict[str, bool]:
    failed()
    restore_gate()
    return {'restart_unblocked': True}


def recovered() -> dict[str, bool]:
    flow.idle()
    ids = flow.record()['containers']
    if (STATE / 'recovery.json').exists() or not flow.running(ids[0]) or flow.running(ids[1]):
        raise RuntimeError('Recovery did not restore exactly the original running containers')
    value = read_record()
    verify_hashes(value['preserved'])
    verify_hashes(value['fixtures'])
    if flow.remote(load()) != flow.record()['remote']:
        raise RuntimeError('Crash changed remote backups')
    if flow.status()['lastsuccess'] != flow.record()['completed']['lastsuccess']:
        raise RuntimeError('Crash changed the last successful backup')
    remove_fixtures()
    return {'running_container_restored': True, 'stopped_container_preserved': True, 'pending_archive_preserved': True}


def rearm() -> dict[str, bool]:
    flow.idle()
    value = read_record()
    if value['fixtures'] or 'workspace' in value or (STATE / 'recovery.json').exists():
        raise RuntimeError('Previous crash has not been recovered')
    value['mode'] = 'reboot'
    save(value)
    (flow.FLOW / 'archiving').unlink(missing_ok=True)
    flow.wait_for_new_minute(load())
    return {'armed': True}
