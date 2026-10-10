"""Debian 12 ABI and service-session checks, exclusively in a disposable container."""

import hashlib
import json
import os
import signal
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from .guest_support import decode, mapping, run
else:
    from guest_support import decode, mapping, run

SOURCE = Path('/src')
STATE = Path('/var/lib/openmediavault-protondrive')
SOCKET = Path('/run/omv-protondrive/control.sock')


def install_dependencies():
    run('apt-get', 'update', '-qq')
    run(
        'apt-get',
        'install',
        '-y',
        '--no-install-recommends',
        'python3',
        'python3-jinja2',
        'python3-yaml',
        'tar',
        'zstd',
        'acl',
        'attr',
        'dbus',
        'gnome-keyring',
        'libsecret-1-0',
        'ca-certificates',
        'php-cli',
        'php-mbstring',
        'php-xml',
        'php-yaml',
        'wget',
        'gnupg',
        'xmlstarlet',
        'lintian',
    )


def configure_session():
    run('useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', 'protondrive')
    for path in (STATE, Path('/data/.omv-protondrive')):
        run('install', '-d', '-o', 'root', '-g', 'protondrive', '-m', '0750', str(path))
    for path in (STATE / 'proton', SOCKET.parent):
        run('install', '-d', '-o', 'protondrive', '-g', 'protondrive', '-m', '0700', str(path))
    model = SOURCE / 'src/omv/datamodels/conf.service.protondrive.json'
    config = {
        key: mapping(value).get('default', '')
        for key, value in mapping(decode(model.read_text())['properties']).items()
    }
    config.update(instanceuuid='a0000000-0000-4000-8000-000000000001', sets=[])
    target = Path('/etc/openmediavault/protondrive.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(config))
    run('chown', 'root:protondrive', str(target))
    target.chmod(0o640)


@contextmanager
def private_session() -> Generator[None, None, None]:
    SOCKET.unlink(missing_ok=True)
    with (
        Path('/tmp/proton-session.log').open('w') as log,
        subprocess.Popen(
            ['runuser', '-u', 'protondrive', '--', '/usr/share/openmediavault-protondrive/session.sh'],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process,
    ):
        try:
            deadline = time.monotonic() + 30
            while not SOCKET.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(Path('/tmp/proton-session.log').read_text())
                time.sleep(0.1)
            yield
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass


def test_private_session():
    previous = None
    for _ in range(2):
        with private_session():
            run('/usr/sbin/omv-protondrive', 'auth-status')
            key = STATE / 'proton/keyring.key'
            assert key.stat().st_mode & 0o777 == 0o600
            digest = hashlib.sha256(key.read_bytes()).digest()
            assert previous is None or previous == digest
            previous = digest


def main():
    if not Path('/.dockerenv').exists() or not Path('/tmp/plugin.deb').is_file():
        raise SystemExit('Requires a disposable Docker container and /tmp/plugin.deb')
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    install_dependencies()
    run('dpkg-deb', '--extract', '/tmp/plugin.deb', '/')
    run('/usr/lib/openmediavault-protondrive/proton-drive', '--help')
    run(
        'python3',
        '-m',
        'unittest',
        'discover',
        '-s',
        '/src/tests/unit',
        '-v',
        env={**os.environ, 'PYTHONPATH': '/src:/src/src:/src/tests/unit:/usr/local/lib/omv-protondrive-vm'},
    )
    configure_session()
    test_private_session()
    run('lintian', '--allow-root', '/tmp/plugin.deb')
    print('PASS: Debian 12 CLI ABI, runtime tests, private keyring restart, package policy')


if __name__ == '__main__':
    main()
