"""Verify extraction metadata and sparse files on the actual Debian guest."""

import os
import subprocess
import tempfile
from pathlib import Path

from protondrive.restore import extract


def command(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60).stdout


def verify(root: Path) -> None:
    source = root / 'source'
    source.mkdir(mode=0o750)
    original = source / 'payload'
    original.write_bytes(b'metadata-preserving restore\n')
    original.chmod(0o640)
    os.chown(original, 12345, 23456)
    os.utime(original, (1_700_000_000, 1_700_000_000))
    os.setxattr(original, 'user.protondrive-test', b'preserved')
    command('setfacl', '-m', 'u:34567:r--', str(original))
    (source / 'link').symlink_to('payload')
    os.link(original, source / 'hard')
    with (source / 'sparse').open('wb') as stream:
        stream.seek(64 * 1024 * 1024)
        stream.write(b'end')
    archive = root / 'fixture.tar'
    command(
        'tar',
        '--create',
        '--numeric-owner',
        '--acls',
        '--xattrs',
        '--xattrs-include=*',
        '--sparse',
        '--file',
        str(archive),
        '--directory',
        str(root),
        'source',
    )
    destination = root / 'restored'
    extract(archive, ['source'], destination, 1024 * 1024, lambda message: print(message, flush=True))
    recovered = destination / 'source/payload'
    before, after = original.stat(), recovered.stat()
    if original.read_bytes() != recovered.read_bytes():
        raise RuntimeError('Restored payload differs')
    if (before.st_uid, before.st_gid, before.st_mode, before.st_mtime_ns) != (
        after.st_uid,
        after.st_gid,
        after.st_mode,
        after.st_mtime_ns,
    ):
        raise RuntimeError('Ownership, mode or modification time was not preserved')
    if command('getfacl', '-cn', str(original)) != command('getfacl', '-cn', str(recovered)):
        raise RuntimeError('POSIX ACL was not preserved')
    if os.getxattr(recovered, 'user.protondrive-test') != b'preserved':
        raise RuntimeError('Extended attribute was not preserved')
    if (destination / 'source/link').readlink() != Path('payload'):
        raise RuntimeError('Symbolic link was not preserved')
    if (destination / 'source/hard').stat().st_ino != after.st_ino:
        raise RuntimeError('Hard link was not preserved')
    sparse = destination / 'source/sparse'
    if sparse.stat().st_size != (source / 'sparse').stat().st_size or sparse.stat().st_blocks * 512 > 1024 * 1024:
        raise RuntimeError('Sparse file was expanded or truncated')
    print('PASS: Debian ownership, permissions, ACLs, xattrs, hard links, symlinks and sparse extraction')


def main() -> None:
    if os.geteuid() != 0 or not Path('/var/lib/protondrive-interactive-vm').is_file():
        raise RuntimeError('Requires root in the interactive development VM')
    with tempfile.TemporaryDirectory(prefix='protondrive-metadata-', dir='/var/tmp') as temporary:
        verify(Path(temporary))


if __name__ == '__main__':
    from console import install_traceback

    install_traceback()
    main()
