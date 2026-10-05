"""Real upload interruption and retry assertions, restricted to the interactive fixture VM."""

import argparse
import hashlib
import json
import os
import pwd
import signal
import tempfile
import time
import uuid
from pathlib import Path

from protondrive.archive import check_space, digest
from protondrive.common import CONFIG, STATE, atomic_json, request
from protondrive.config import load, remote_folder, validate
from protondrive.json_data import JSONValue, decode, integer, object_value
from protondrive.models import RemoteEntry
from protondrive.retention import metadata, unique
from protondrive.runner import receipt

if __package__:
    from . import live_ui_guest as flow
    from .flow_records import RetryRecord, retry_record
    from .live_proton_guest import download_pair, latest_complete_archive, service_pid, verify_restore
else:
    import live_ui_guest as flow
    from flow_records import RetryRecord, retry_record
    from live_proton_guest import download_pair, latest_complete_archive, service_pid, verify_restore

TABLE = 'omv_protondrive_ui_fault'
FAULT_UNIT = 'omv-protondrive-ui-fault.service'
PAYLOAD = Path('/data/interactive-fixtures/system/upload-retry.bin')
PAYLOAD_BYTES = 32 * 1024 * 1024
MIN_SENT = 512 * 1024


def read_record() -> RetryRecord:
    return retry_record(decode((flow.FLOW / 'retry.json').read_text()))


def save_record(value: RetryRecord) -> None:
    atomic_json(flow.FLOW / 'retry.json', value)


def nft(*args: str) -> bytes:
    return flow.run('nft', *args).stdout


def nft_items(*args: str) -> list[dict[str, JSONValue]]:
    values = object_value(decode(nft('-j', *args)))['nftables']
    if not isinstance(values, list):
        raise TypeError('Invalid nftables response')
    return [object_value(entry) for entry in values]


def table() -> dict[str, JSONValue] | None:
    tables = [object_value(entry['table']) for entry in nft_items('list', 'ruleset') if 'table' in entry]
    return next(
        (entry for entry in tables if entry.get('name') == TABLE and entry.get('family') == 'inet'),
        None,
    )


def restore_network() -> None:
    current = table()
    if current is None:
        return
    if current.get('comment') != read_record()['token']:
        raise RuntimeError('Refusing to remove a firewall table not owned by this flow')
    nft('delete', 'table', 'inet', TABLE)


def disarm() -> None:
    if flow.run('systemctl', 'list-units', '--all', '--plain', '--no-legend', FAULT_UNIT).stdout.strip():
        flow.run('systemctl', 'stop', FAULT_UNIT)
    restore_network()


def remove_fixture() -> None:
    value = read_record()
    if not PAYLOAD.exists():
        return
    if PAYLOAD.is_symlink() or [PAYLOAD.stat().st_dev, PAYLOAD.stat().st_ino] != value.get('payload_identity'):
        raise RuntimeError('Retry payload identity changed; refusing removal')
    if value.get('payload_sha256') and digest(PAYLOAD) != value['payload_sha256']:
        raise RuntimeError('Retry payload contents changed; refusing removal')
    PAYLOAD.unlink()


def prepare() -> dict[str, object]:
    flow.idle()
    config = load()
    flow.guard(config)
    if (flow.FLOW / 'retry.json').exists() or PAYLOAD.exists() or PAYLOAD.is_symlink() or table() is not None:
        raise RuntimeError('Existing retry fixture or firewall table needs inspection')
    baseline = flow.remote(config)
    for item in config['sets']:
        latest_complete_archive(baseline[item['uuid']], item['name'])
    for item in config['sets']:
        directory = Path(config['stagingpath']) / item['uuid']
        if any(
            not receipt(path.with_name(path.name.removesuffix('.manifest.json'))).exists()
            for path in directory.glob('*.tar.zst.manifest.json')
        ):
            raise RuntimeError('Existing pending uploads must be resolved before fault injection')
    flow.wait_for_new_minute(config)
    check_space(PAYLOAD.parent, PAYLOAD_BYTES * 3, config['minimumfreebytes'])
    value = retry_record(
        {'token': str(uuid.uuid4()), 'remote': baseline, 'completed': flow.status(), 'injected': False}
    )
    save_record(value)
    with PAYLOAD.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        value['payload_identity'] = [os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino]
        save_record(value)
        for _ in range(PAYLOAD_BYTES // (1024 * 1024)):
            stream.write(os.urandom(1024 * 1024))
    value['payload_sha256'] = digest(PAYLOAD)
    save_record(value)
    config['transfertimeout'] = 60
    # Keep the interrupted archive available for its explicit restore check after the retry run.
    for item in config['sets']:
        item['localkeep'] = max(2, item['localkeep'])
        item['remotekeep'] = max(2, item['remotekeep'])
    flow.prepare_containers(config, flow.record())
    flow.run('systemctl', 'restart', 'omv-protondrive')
    helper = Path(__file__).resolve()
    flow.run(
        'systemd-run',
        '--quiet',
        '--collect',
        '--service-type=exec',
        f'--unit={FAULT_UNIT}',
        '--property=RuntimeMaxSec=240s',
        '--property=TimeoutStopSec=15s',
        f'--property=ExecStopPost=/usr/bin/python3 {helper} restore-network',
        '--setenv=PYTHONPATH=/usr/share/openmediavault-protondrive',
        '/usr/bin/python3',
        str(helper),
        'watch',
    )
    deadline = time.monotonic() + 15
    while not read_record().get('armed'):
        if time.monotonic() >= deadline:
            raise RuntimeError('Upload fault watcher did not arm')
        time.sleep(0.1)
    return {'armed': True, 'payload_bytes': PAYLOAD_BYTES, 'network_scope': 'VM protondrive user HTTPS only'}


def counter(name: str) -> int:
    items = nft_items('list', 'counter', 'inet', TABLE, name)
    return next(integer(object_value(entry['counter'])['bytes']) for entry in items if 'counter' in entry)


def watch() -> None:
    config = load()
    flow.guard(config)
    value = read_record()
    uid = str(pwd.getpwnam('protondrive').pw_uid)
    try:
        if table() is not None:
            raise RuntimeError('Fault table already exists')
        nft('add', 'table', 'inet', TABLE, '{', 'comment', json.dumps(value['token']), ';', '}')
        nft('add', 'chain', 'inet', TABLE, 'output', '{ type filter hook output priority -10; policy accept; }')
        for name in ('sent', 'blocked'):
            nft('add', 'counter', 'inet', TABLE, name)
        nft(
            'add',
            'rule',
            'inet',
            TABLE,
            'output',
            'meta',
            'skuid',
            uid,
            'tcp',
            'dport',
            '443',
            'counter',
            'name',
            'sent',
        )
        value['armed'] = True
        save_record(value)
        deadline = time.monotonic() + 210
        initial_bytes = 0
        target = None
        while time.monotonic() < deadline:
            activity = request('status')
            if activity.get('transferphase') == 'Archive upload' and activity.get('transferfile', '').startswith(
                'system-'
            ):
                name = activity['transferfile']
                if target is None:
                    target, initial_bytes = name, counter('sent')
                if name != target:
                    raise RuntimeError('Upload changed before network interruption')
                sent = counter('sent') - initial_bytes
                if sent >= MIN_SENT:
                    nft(
                        'add',
                        'rule',
                        'inet',
                        TABLE,
                        'output',
                        'meta',
                        'skuid',
                        uid,
                        'tcp',
                        'dport',
                        '443',
                        'counter',
                        'name',
                        'blocked',
                        'reject',
                        'with',
                        'tcp',
                        'reset',
                    )
                    value.update(injected=True, archive=name, network_bytes_before_fault=sent)
                    save_record(value)
                    break
            elif target is not None:
                raise RuntimeError('Upload ended before the fault could interrupt it')
            time.sleep(0.05)
        else:
            raise RuntimeError('Timed out waiting for real upload traffic')
        while time.monotonic() < deadline:
            current = flow.status()
            if current.get('phase') == 'failed' and not flow.active():
                value['blocked_bytes'] = counter('blocked')
                if not value['blocked_bytes']:
                    raise RuntimeError('No HTTPS traffic was blocked')
                value['failure_observed'] = True
                save_record(value)
                return
            time.sleep(0.2)
        raise RuntimeError('Backup did not fail while the network fault was active')
    except BaseException as error:
        value['watcher_error'] = str(error)
        save_record(value)
        raise
    finally:
        restore_network()


def confirmed_unchanged(before: list[RemoteEntry], after: list[RemoteEntry]) -> None:
    for entry in before:
        if unique(after, entry['name'], entry['type']) != entry:
            raise RuntimeError('An existing remote entry changed during the failed upload')


def failed() -> dict[str, object]:
    flow.idle()
    value = read_record()
    if not value.get('injected') or not value.get('failure_observed') or value.get('watcher_error'):
        raise RuntimeError(f'Upload fault was not proven: {value.get("watcher_error", "missing evidence")}')
    disarm()
    config = load()
    current = flow.status()
    if current.get('phase') != 'failed' or current.get('lastsuccess') != value['completed'].get('lastsuccess'):
        raise RuntimeError('Failed upload changed the successful backup status')
    item = next(item for item in config['sets'] if item['name'] == 'system')
    path = Path(config['stagingpath']) / item['uuid'] / value['archive']
    manifest = path.with_name(path.name + '.manifest.json')
    contents = metadata(decode(manifest.read_text()), config, item, path.name)
    if not path.is_file() or digest(path) != contents['sha256'] or receipt(path).exists():
        raise RuntimeError('Interrupted upload did not retain an unconfirmed complete local archive')
    listing = flow.remote(config)
    for key, before in value['remote'].items():
        confirmed_unchanged(before, listing[key])
    if any(entry['name'] == manifest.name for entry in listing[item['uuid']]):
        raise RuntimeError('Interrupted upload published a completion manifest')
    if list(Path(config['stagingpath']).glob('*/*.partial')) or (STATE / 'recovery.json').exists():
        raise RuntimeError('Failed backup left partial archives or unrecovered containers')
    containers = flow.record()['containers']
    if not flow.running(containers[0]) or flow.running(containers[1]):
        raise RuntimeError('Failed backup did not preserve the original container states')
    # Remove the large source before the new cycle; the pending archive must still retry unchanged.
    remove_fixture()
    config['transfertimeout'] = validate(decode((flow.FLOW / 'config.json').read_text()))['transfertimeout']
    atomic_json(flow.FLOW / 'fixture-config.json', config)
    atomic_json(CONFIG, config)
    flow.run('systemctl', 'restart', 'omv-protondrive')
    value.update(pending_sha256=contents['sha256'], retry_since=time.time(), setuuid=item['uuid'])
    save_record(value)
    return {
        'failed_safely': True,
        'archive': path.name,
        'network_bytes_before_fault': value['network_bytes_before_fault'],
        'blocked_bytes': value['blocked_bytes'],
        'lastsuccess': current.get('lastsuccess', ''),
    }


def verify() -> dict[str, object]:
    flow.idle()
    value = read_record()
    config = load()
    item = next(item for item in config['sets'] if item['uuid'] == value['setuuid'])
    path = Path(config['stagingpath']) / item['uuid'] / value['archive']
    marker = receipt(path)
    confirmation = decode(marker.read_text())
    if confirmation != {'folder': remote_folder(config, item), 'sha256': value['pending_sha256']}:
        raise RuntimeError('Retry did not confirm the original pending archive')
    if flow.status().get('phase') != 'completed':
        raise RuntimeError('Retry run did not complete')
    cid = flow.record()['containers'][0]
    events = flow.run(
        'docker',
        'events',
        '--since',
        str(value['retry_since']),
        '--until',
        str(time.time()),
        '--filter',
        f'container={cid}',
        '--filter',
        'event=die',
        '--format',
        '{{json .}}',
    ).stdout
    stopped = [integer(object_value(decode(line))['timeNano']) for line in events.splitlines()]
    if not stopped or marker.stat().st_mtime_ns >= min(stopped):
        raise RuntimeError('Pending upload was not confirmed before containers stopped')
    flow.report_activity('Download retried archive', path.name)
    with tempfile.TemporaryDirectory(prefix='live-proton-', dir='/run/omv-protondrive') as temporary:
        scratch = Path(temporary)
        download_pair(service_pid(), remote_folder(config, item), path.name, scratch, flow.report_activity)
        downloaded = scratch / path.name
        if digest(downloaded) != value['pending_sha256']:
            raise RuntimeError('Retried archive checksum mismatch')
        manifest = metadata(decode((scratch / (path.name + '.manifest.json')).read_text()), config, item, path.name)
        if manifest['sha256'] != value['pending_sha256'] or downloaded.stat().st_size != manifest['size']:
            raise RuntimeError('Retried remote manifest disagrees with the retained archive')
        verify_restore(downloaded, Path(item['paths']) / 'example.txt')
        # Verify the interrupted payload itself, not only the small example file.
        extracted = flow.run(
            'tar', '--extract', '--zstd', '--to-stdout', '--file', str(downloaded), str(PAYLOAD.relative_to('/'))
        ).stdout
        if len(extracted) != PAYLOAD_BYTES or hashlib.sha256(extracted).hexdigest() != value['payload_sha256']:
            raise RuntimeError('Retried payload did not restore intact')
    containers = flow.record()['containers']
    if not flow.running(containers[0]) or flow.running(containers[1]):
        raise RuntimeError('Retry did not recover the original container states')
    return {
        'retried_before_container_stop': True,
        'archive': path.name,
        'restored': True,
        'payload_bytes': PAYLOAD_BYTES,
    }


class Arguments(argparse.Namespace):
    action: str = ''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['watch', 'restore-network'])
    action = parser.parse_args(namespace=Arguments()).action
    signal.signal(signal.SIGTERM, flow.interrupted)
    flow.guard(load())
    if action == 'watch':
        watch()
    else:
        restore_network()


if __name__ == '__main__':
    from console import install_traceback

    install_traceback()
    main()
