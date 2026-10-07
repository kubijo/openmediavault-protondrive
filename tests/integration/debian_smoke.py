"""Run the Debian ABI/session tests in an automatically removed disposable container."""

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from cli_options import parse_options
from tool_data import decode, mapping, string

SOURCE = Path(__file__).resolve().parents[2]
DEBIAN = mapping(decode((SOURCE / 'config/sources.json').read_text())['debian-container'])
IMAGE = f'{string(DEBIAN["image"])}:{string(DEBIAN["tag"])}@{string(DEBIAN["digest"])}'


@dataclass
class Options:
    guest_bundle: Path
    """Built guest helpers and locked portable test dependencies, supplied by Nix."""
    package: Path = Path('result/openmediavault-protondrive_7.0.0_amd64.deb')
    """Built Debian package to test; build it with `just app::build` first."""


def main(options: Options):
    package = options.package.resolve(strict=True)
    bundle = options.guest_bundle.resolve(strict=True)
    source = SOURCE
    name = f'protondrive-test-{uuid.uuid4().hex}'
    try:
        subprocess.run(
            [
                'docker',
                'run',
                '--detach',
                '--name',
                name,
                '--mount',
                f'type=bind,src={source},dst=/src,readonly',
                IMAGE,
                'sleep',
                'infinity',
            ],
            check=True,
        )
        subprocess.run(['docker', 'cp', str(package), f'{name}:/tmp/plugin.deb'], check=True)
        subprocess.run(['docker', 'cp', str(bundle), f'{name}:/tmp/guest-tools.tar'], check=True)
        subprocess.run(
            ['docker', 'exec', name, 'tar', '-xf', '/tmp/guest-tools.tar', '-C', '/usr/local/lib'], check=True
        )
        # The minimal base image has no Python. Bootstrap just the guest interpreter.
        subprocess.run(['docker', 'exec', name, 'apt-get', 'update', '-qq'], check=True)
        subprocess.run(
            ['docker', 'exec', name, 'apt-get', 'install', '-y', '--no-install-recommends', 'python3'], check=True
        )
        subprocess.run(['docker', 'exec', name, 'python3', '/src/tests/integration/debian_guest.py'], check=True)
    finally:
        subprocess.run(['docker', 'rm', '--force', name], check=False)


if __name__ == '__main__':
    main(parse_options(Options))
