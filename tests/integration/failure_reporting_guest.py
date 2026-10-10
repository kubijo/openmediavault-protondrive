"""Check the installed controller's public error and matching journal entry."""

import os
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from protondrive.common import BackupError, request
from protondrive_api.transport import BrokerClient
from protondrive_api.v1 import control_pb2 as wire

REMOTE = Path('/var/lib/openmediavault-protondrive/proton/fake-remote')


def browse() -> wire.BrokerResponse:
    return BrokerClient().call(wire.BrokerRequest(actor='admin', backups=wire.BrowseBackupsRequest()))


def expect_error(response: wire.BrokerResponse, code: str, message: str) -> None:
    if not response.HasField('error') or response.error.code != code or response.error.message != message:
        raise AssertionError(f'Expected {code}: {response}')


def service_unavailable() -> None:
    unit = 'omv-protondrive.service'
    subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True, timeout=10)
    subprocess.run(['systemctl', 'stop', unit], check=True, timeout=30)
    try:
        expect_error(browse(), 'unavailable', 'Proton service is unavailable; retry after it starts')
    finally:
        subprocess.run(['systemctl', 'start', unit], check=True, timeout=30)
        deadline = time.monotonic() + 20
        while True:
            try:
                request('status', timeout=2)
                break
            except BackupError as error:
                if error.code != 'unavailable' or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
    print('PASS: Proton outage returns unavailable; service restored', flush=True)


def sign_in_required() -> None:
    marker = REMOTE / 'force-signed-out'
    marker.touch(exist_ok=False)
    try:
        expect_error(browse(), 'sign_in_required', 'Sign in to Proton Drive')
    finally:
        marker.unlink()
        if request('probe', timeout=10)['state'] != 'signed-in':
            raise AssertionError('Fake Proton account did not recover after sign-in fault')
    print('PASS: signed-out Proton CLI returns sign_in_required', flush=True)


def service_busy() -> None:
    hold, ready, release = (REMOTE / name for name in ('hold-list', 'hold-ready', 'release-list'))
    if ready.exists() or release.exists():
        raise RuntimeError('Stale Proton list hold fixture')
    hold.touch(exist_ok=False)
    worker = ThreadPoolExecutor(max_workers=1)
    try:
        first = worker.submit(browse)
        deadline = time.monotonic() + 10
        while not ready.exists():
            if first.done():
                raise AssertionError(f'First browse ended before its hold: {first.result()}')
            if time.monotonic() >= deadline:
                raise AssertionError('Proton list did not reach its hold')
            time.sleep(0.05)
        expect_error(browse(), 'busy', 'Storage service is busy; retry after the current operation')
        release.touch()
        if first.result(timeout=25).HasField('error'):
            raise AssertionError('Held browse did not recover after release')
    finally:
        release.touch()
        worker.shutdown(wait=True)
        for path in (hold, ready, release):
            path.unlink(missing_ok=True)
    print('PASS: concurrent Proton browse returns busy; held browse released', flush=True)


def invalid_archive() -> None:
    root = REMOTE / 'my-files/open-media-vault-proton-backup-development'
    archives = [path for path in root.glob('*/*/*.tar.zst') if path.with_name(path.name + '.manifest.json').is_file()]
    if not archives:
        raise AssertionError('No completed fixture archive available to corrupt')
    source = min(archives)
    with source.open('r+b') as stream:
        original = stream.read(1)
        if not original:
            raise AssertionError('Fixture archive is empty')
        try:
            stream.seek(0)
            stream.write(bytes([original[0] ^ 0xFF]))
            stream.flush()
            os.fsync(stream.fileno())
            job_id = str(uuid.uuid4())
            response = BrokerClient().call(
                wire.BrokerRequest(
                    actor='admin',
                    start_operation=wire.StartOperationRequest(
                        request_id=job_id,
                        operation=wire.OPERATION_INSPECT_ARCHIVE,
                        inspect_archive=wire.ArchiveReference(
                            instance_id=source.parent.parent.name,
                            set_id=source.parent.name,
                            name=source.name,
                        ),
                    ),
                )
            )
            if response.HasField('error'):
                raise AssertionError(f'Corrupt archive inspection was not admitted: {response.error}')
            deadline = time.monotonic() + 60
            while True:
                result = BrokerClient().call(wire.BrokerRequest(actor='admin', job=wire.GetJobRequest(id=job_id)))
                if result.HasField('error'):
                    raise AssertionError(f'Cannot read corrupt archive job: {result.error}')
                if result.job.job.state == wire.JOB_STATE_FAILED:
                    if (
                        result.job.job.failure_code != 'invalid_archive'
                        or result.job.job.message != 'Downloaded archive failed checksum verification'
                    ):
                        raise AssertionError(f'Corrupt archive reported the wrong failure: {result.job.job}')
                    break
                if (
                    result.job.job.state in (wire.JOB_STATE_SUCCEEDED, wire.JOB_STATE_CANCELLED)
                    or time.monotonic() >= deadline
                ):
                    raise AssertionError(f'Corrupt archive did not fail safely: {result.job.job}')
                time.sleep(0.1)
        finally:
            stream.seek(0)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    print('PASS: corrupt archive job returns invalid_archive; fixture restored', flush=True)


def main() -> None:
    if os.geteuid() != 0 or not Path('/run/protondrive-disposable-test').is_file():
        raise RuntimeError('Requires root in a disposable regression VM')

    service_unavailable()
    sign_in_required()
    service_busy()
    invalid_archive()
    response = BrokerClient().call(
        wire.BrokerRequest(
            actor='admin',
            directories=wire.BrowseDirectoriesRequest(
                shared_folder_id='ffffffff-ffff-4fff-8fff-ffffffffffff',
            ),
        )
    )
    if not response.HasField('error') or response.error.code != 'internal':
        raise AssertionError(f'Unknown shared folder did not produce a controller error: {response}')
    match = re.fullmatch(r'Controller operation failed; reference ([a-f0-9]{12})', response.error.message)
    if match is None:
        raise AssertionError(f'Controller returned an unexpected public error: {response.error.message}')

    reference = match.group(1)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        journal = subprocess.run(
            ['journalctl', '--unit=omv-protondrive-controller', '--no-pager', '--output=cat', '--lines=200'],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        if f'Controller operation failed; reference {reference}' in journal:
            print(f'PASS: public internal reference {reference} matches controller journal', flush=True)
            return
        time.sleep(0.2)
    raise AssertionError(f'Controller journal did not contain reference {reference}')


if __name__ == '__main__':
    main()
