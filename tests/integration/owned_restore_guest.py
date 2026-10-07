"""Own and verify only the selected-file restore probe's local VM fixtures."""

import json
import os
import shutil
import stat
import subprocess
import tarfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import tyro

from cli_options import parse_options
from protondrive.common import STATE, BackupError, atomic_json, locked, request
from protondrive.config import identifier, load
from protondrive.json_data import decode, object_value, string
from protondrive.records import listing

if __package__:
    from . import owned_restore_faults as faults
    from .live_ui_guest import guard
else:
    import owned_restore_faults as faults
    from live_ui_guest import guard

ROOT = Path('/var/lib/protondrive-owned-ui-restore')
SOURCE = Path('/data/interactive-fixtures/system/example.txt')


def owned(token: str) -> Path:
    directory = ROOT / identifier(token)
    for path in (ROOT, directory):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError('Restore fixture directory is not private and owned')
    record = object_value(decode((directory / 'owner.json').read_text()))
    if record.get('token') != token:
        raise RuntimeError('Restore fixture ownership does not match')
    return directory


def prepare(token: str) -> dict[str, str]:
    token = identifier(token)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    root_info = ROOT.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid() or root_info.st_mode & 0o077:
        raise RuntimeError('Restore fixture parent is not private and owned')
    directory = ROOT / token
    directory.mkdir(mode=0o700)
    atomic_json(directory / 'owner.json', {'token': token, 'expected': SOURCE.read_text()})
    return {'destination': str(directory / 'files')}


def verify(token: str, inspection_id: str | None = None) -> dict[str, bool]:
    directory = owned(token)
    restored = directory / 'files' / SOURCE.relative_to('/')
    for path in (restored, *restored.parents):
        if path == directory:
            break
        if path.is_symlink():
            raise RuntimeError('Unexpected symlink in selected file restoration')
    record = object_value(decode((directory / 'owner.json').read_text()))
    if restored.read_text() != record['expected']:
        raise RuntimeError('Restored fixture content differs from the source')
    if (directory / 'files/data/interactive-fixtures/appData').exists():
        raise RuntimeError('Restoration included an unselected set')
    if inspection_id is not None:
        archive = STATE / 'restore-cache' / identifier(inspection_id) / 'archive.tar'
        with tarfile.open(archive) as contents:
            member = contents.getmember(str(SOURCE.relative_to('/')))
            info = restored.stat()
            mtime = int(Decimal(str(member.pax_headers.get('mtime', member.mtime))) * 1_000_000_000)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_mtime_ns) != (
                member.uid,
                member.gid,
                member.mode,
                mtime,
            ):
                raise RuntimeError('Restored ownership, permissions or modification time differ from the archive')
            for name, value in member.pax_headers.items():
                if name.startswith('SCHILY.xattr.') and os.getxattr(
                    restored, name.removeprefix('SCHILY.xattr.')
                ) != value.encode('utf-8', 'surrogateescape'):
                    raise RuntimeError('Restored extended attribute differs from the archive')
            if 'SCHILY.acl.access' in member.pax_headers:
                actual = subprocess.run(
                    ['getfacl', '-cnE', str(restored)], check=True, capture_output=True, text=True, timeout=10
                ).stdout
                expected = member.pax_headers['SCHILY.acl.access'].replace('\n', ',').split(',')
                if {line.strip() for line in actual.splitlines() if line.strip()} != {
                    line.strip() for line in expected if line.strip()
                }:
                    raise RuntimeError('Restored POSIX ACL differs from the archive')
    return {'verified': True, 'metadata': inspection_id is not None}


def cleanup(token: str) -> dict[str, bool]:
    directory = ROOT / identifier(token)
    if (ROOT / 'active').exists() or (ROOT / 'active').is_symlink():
        faults.disarm(owned(token))
    with locked(STATE / 'run.lock'):
        if not directory.exists() and not directory.is_symlink():
            return {'cleaned': True}
        shutil.rmtree(owned(token))
    return {'cleaned': True}


def foreign(token: str) -> dict[str, str]:
    directory = owned(token)
    config = load()
    for entry in request('browse', timeout=25):
        if entry['type'] != 'folder' or entry['name'] == config['instanceuuid']:
            continue
        try:
            instance = identifier(entry['name'])
        except BackupError:
            continue
        entries = request('browse', timeout=25, instanceuuid=instance)
        atomic_json(directory / 'foreign.json', {'instance': instance, 'entries': entries})
        return {'instance': instance}
    raise RuntimeError('No foreign instance exists under the development root; no remote fixture was created')


def verify_foreign(token: str) -> dict[str, bool]:
    record = object_value(decode((owned(token) / 'foreign.json').read_text()))
    entries = request('browse', timeout=25, instanceuuid=identifier(string(record['instance'])))
    if sorted(entries, key=lambda item: item['name']) != sorted(
        listing(record['entries']), key=lambda item: item['name']
    ):
        raise RuntimeError('Foreign instance listing changed during read-only browsing')
    return {'unchanged': True}


@dataclass
class Arguments:
    """Own and verify only the selected-file restore probe's local VM fixtures."""

    action: tyro.conf.Positional[
        Literal['prepare', 'verify', 'cleanup', 'arm', 'held', 'crash', 'disarm', 'absent', 'foreign', 'verify-foreign']
    ]
    """Fixture operation to perform."""
    token: tyro.conf.Positional[str]
    """UUID identifying this probe's owned fixtures."""
    inspection_id: str | None = None
    """Verified archive whose metadata must match the extracted file."""


def main() -> None:
    args = parse_options(Arguments)
    guard(load())
    match args.action:
        case 'prepare':
            print(json.dumps(prepare(args.token)))
        case 'verify':
            print(json.dumps(verify(args.token, args.inspection_id) if args.inspection_id else verify(args.token)))
        case 'cleanup':
            print(json.dumps(cleanup(args.token)))
        case 'arm':
            print(json.dumps(faults.arm(owned(args.token))))
        case 'held':
            print(json.dumps(faults.held(owned(args.token))))
        case 'crash':
            print(json.dumps(faults.crash(owned(args.token))))
        case 'disarm':
            print(json.dumps(faults.disarm(owned(args.token))))
        case 'absent':
            print(json.dumps(faults.verify_absent(owned(args.token))))
        case 'foreign':
            print(json.dumps(foreign(args.token)))
        case 'verify-foreign':
            print(json.dumps(verify_foreign(args.token)))


if __name__ == '__main__':
    from console import install_traceback

    install_traceback()
    main()
