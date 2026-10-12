#!/usr/bin/env python3
"""Restore per-VM SSH access from the test-only seed ISO after OMV removes cloud-init."""

import subprocess
import time
from pathlib import Path

MARKER = Path('/etc/omv-protondrive-test-vm')
DEVICE = Path('/dev/disk/by-label/cidata')
MOUNT = Path('/run/omv-protondrive-seed')
SSH = Path('/root/.ssh')


def apply_key(seed: Path, destination: Path) -> None:
    key = (seed / 'authorized-key').read_text().strip()
    if not key.startswith('ssh-ed25519 ') or '\n' in key:
        raise ValueError('Invalid test VM SSH key')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.chmod(0o700)
    target = destination / 'authorized_keys'
    target.write_text(key + '\n')
    target.chmod(0o600)


def main() -> None:
    if not MARKER.is_file():
        raise SystemExit('Only disposable OMV test VMs may load a seed identity')
    deadline = time.monotonic() + 20
    while not DEVICE.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Test VM seed ISO did not appear')
        time.sleep(0.2)
    MOUNT.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(['mount', '-t', 'iso9660', '-o', 'ro', str(DEVICE), str(MOUNT)], check=True)
    try:
        apply_key(MOUNT, SSH)
    finally:
        subprocess.run(['umount', str(MOUNT)], check=True)
    subprocess.run(['ssh-keygen', '-A'], check=True)


if __name__ == '__main__':
    main()
