"""Build a session-free image or initialize a fresh deterministic regression overlay."""

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tyro

from cli_options import parse_options
from console import install_traceback

if __package__:
    from . import interactive_guest, provision_guest
    from .guest_support import decode, run
else:
    import interactive_guest
    import provision_guest
    from guest_support import decode, run

STATE = Path('/var/lib/openmediavault-protondrive')
BINARY = Path('/usr/lib/openmediavault-protondrive/proton-drive')
TEMPLATE = Path('/var/lib/protondrive-regression-template')


@dataclass
class Options:
    action: tyro.conf.Positional[Literal['install', 'seal', 'initialize', 'metadata']]
    package: Path | None = None
    configuration: Path | None = None


def install(package: Path) -> None:
    Path('/var/lib/protondrive-interactive-vm').touch()
    interactive_guest.main(package)
    TEMPLATE.touch(mode=0o600)


def seal() -> None:
    if not TEMPLATE.is_file() or (STATE / 'proton/fake-remote').exists():
        raise RuntimeError('Only an untested regression template may be sealed')
    run('systemctl', 'stop', 'omv-protondrive-api', 'omv-protondrive-controller', 'omv-protondrive')
    # Clones must not inherit keyrings or job state.
    shutil.rmtree(STATE, ignore_errors=False)
    provision_guest.seal_identity()


def initialize(configuration: Path) -> None:
    if not TEMPLATE.is_file():
        raise RuntimeError('Expected a freshly cloned regression template')
    # Each clone needs its own backup identity.
    config = decode(run('omv-confdbadm', 'read', 'conf.service.protondrive', capture_output=True, text=True).stdout)
    config['instanceuuid'] = str(uuid.uuid4())
    run('omv-confdbadm', 'update', 'conf.service.protondrive', json.dumps(config))
    run('systemctl', 'restart', 'openmediavault-engined')
    interactive_guest.initialize(configuration)
    run('systemctl', 'stop', 'omv-protondrive')
    shutil.copyfile(Path(__file__).with_name('fake_proton_filesystem.py'), BINARY)
    BINARY.chmod(0o755)
    interactive_guest.apply_configuration(*interactive_guest.INSTALL_MODULES)
    foreign = (
        STATE
        / 'proton/fake-remote/my-files/open-media-vault-proton-backup-development'
        / 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
    )
    run('runuser', '-u', 'protondrive', '--', 'mkdir', '-p', str(foreign))
    run('runuser', '-u', 'protondrive', '--', 'touch', str(foreign / 'foreign-fixture'))
    run('systemctl', 'restart', 'omv-protondrive', 'omv-protondrive-controller', 'omv-protondrive-api')
    run('systemctl', 'is-active', 'nginx', 'omv-protondrive', 'omv-protondrive-controller', 'omv-protondrive-api')
    TEMPLATE.unlink()
    print('PASS: fresh identity, disabled schedule, isolated fixtures and filesystem-backed Proton', flush=True)


def main(options: Options) -> None:
    if os.geteuid() != 0 or not Path('/run/protondrive-disposable-test').is_file():
        raise RuntimeError('Requires root in a disposable regression VM')
    match options.action:
        case 'install' if options.package is not None:
            install(options.package)
        case 'initialize' if options.configuration is not None:
            initialize(options.configuration)
        case 'seal':
            seal()
        case 'metadata':
            run(
                'env',
                'PYTHONPATH=/usr/share/openmediavault-protondrive',
                'python3',
                str(Path(__file__).with_name('restore_metadata.py')),
            )
        case _:
            raise ValueError('Install needs --package; initialize needs --configuration')


if __name__ == '__main__':
    install_traceback()
    main(parse_options(Options))
