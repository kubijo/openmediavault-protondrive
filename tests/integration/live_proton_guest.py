"""Authenticated remote download and scratch restore for the dedicated test VM."""

import json
import os
import pwd
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from protondrive.archive import digest
from protondrive.common import request
from protondrive.config import load, remote_folder
from protondrive.json_data import decode
from protondrive.models import RemoteEntry
from protondrive.retention import metadata, unique

if __package__:
    from .flow_records import RestoreResult
else:
    from flow_records import RestoreResult

DOWNLOAD_HELPER = Path(__file__).resolve().with_name('live_proton_download.py')


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


def latest_complete_archive(listing: list[RemoteEntry], set_name: str) -> str:
    pattern = re.compile(re.escape(set_name) + r'-\d{8}T\d{4}Z\.tar\.zst')
    archives: list[str] = []
    for entry in listing:
        name = entry['name']
        if entry['type'] != 'file' or not pattern.fullmatch(name):
            continue
        if not any(other['name'] == name + '.manifest.json' for other in listing):
            continue
        unique(listing, name)
        unique(listing, name + '.manifest.json')
        archives.append(name)
    if not archives:
        raise RuntimeError(f'No completed remote archive for {set_name}')
    return max(archives)


def download_pair(
    pid: int, folder: str, name: str, scratch: Path, on_activity: Callable[[str, str], None] | None = None
) -> None:
    user = pwd.getpwnam('protondrive')
    os.chown(scratch, user.pw_uid, user.pw_gid)
    scratch.chmod(0o700)
    paths = [folder + '/' + name, folder + '/' + name + '.manifest.json']
    command = ['nsenter', '-t', str(pid), '-m', '--', 'python3', str(DOWNLOAD_HELPER), '--pid', str(pid)]
    command.extend(['--directory', str(scratch)])
    for path in paths:
        if on_activity:
            phase = 'Restore manifest download' if path.endswith('.manifest.json') else 'Restore archive download'
            on_activity(phase, Path(path).name)
        subprocess.run([*command, '--remote', path], check=True, capture_output=True, text=True, timeout=200)


def verify_restore(archive: Path, source: Path) -> None:
    subprocess.run(['zstd', '--test', str(archive)], check=True, capture_output=True)
    with tempfile.TemporaryDirectory(prefix='live-proton-restore-') as restored:
        subprocess.run(
            [
                'tar',
                '--extract',
                '--zstd',
                '--numeric-owner',
                '--same-owner',
                '--same-permissions',
                '--acls',
                '--xattrs',
                '--xattrs-include=*',
                '--file',
                str(archive),
                '--directory',
                restored,
            ],
            check=True,
            capture_output=True,
        )
        destination = Path(restored) / source.relative_to('/')
        if source.read_bytes() != destination.read_bytes():
            raise RuntimeError('Restored fixture content mismatch')
        original, recovered = source.stat(), destination.stat()
        if (original.st_uid, original.st_gid, original.st_mode & 0o7777) != (
            recovered.st_uid,
            recovered.st_gid,
            recovered.st_mode & 0o7777,
        ):
            raise RuntimeError('Restored fixture metadata mismatch')


def verify_backups(on_activity: Callable[[str, str], None] | None = None) -> list[RestoreResult]:
    if Path(__file__).resolve().parent != Path('/usr/local/lib/omv-protondrive-vm').resolve(strict=True):
        raise RuntimeError('Run the built VM helper from /usr/local/lib/omv-protondrive-vm')
    config = load()
    pid = service_pid()
    results: list[RestoreResult] = []
    for item in config['sets']:
        if on_activity:
            on_activity('Find completed backup', item['name'])
        listing = request('prepare', setuuid=item['uuid'])
        name = latest_complete_archive(listing, item['name'])
        folder = remote_folder(config, item)
        with tempfile.TemporaryDirectory(prefix='live-proton-', dir='/run/omv-protondrive') as temporary:
            scratch = Path(temporary)
            download_pair(pid, folder, name, scratch, on_activity)
            if on_activity:
                on_activity('Verify downloaded checksum', name)
            archive = scratch / name
            manifest = metadata(decode((scratch / (name + '.manifest.json')).read_text()), config, item, name)
            if archive.stat().st_size != manifest['size'] or digest(archive) != manifest['sha256']:
                raise RuntimeError('Downloaded archive checksum mismatch')
            if on_activity:
                on_activity('Extract and verify restore', name)
            verify_restore(archive, Path(item['paths'].strip()) / 'example.txt')
            results.append({'set': item['name'], 'archive': name, 'bytes': manifest['size'], 'restored': True})
    return results


def main() -> None:
    print(json.dumps(verify_backups(), indent=2))


if __name__ == '__main__':
    main()
