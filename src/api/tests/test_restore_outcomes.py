"""Exercise real extraction across publication and cleanup failures."""

import errno
import io
import os
import tarfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from helpers import configuration
from protondrive.restore_journal import Publication
from protondrive_api.archives import Archives
from protondrive_api.broker import Controller
from protondrive_api.omv import OMV
from protondrive_api.store import Store
from protondrive_api.v1 import control_pb2 as wire


@pytest.fixture(autouse=True)
def private_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('protondrive_api.archives.STATE', tmp_path)


def setup(root: Path) -> tuple[Controller, wire.StartOperationRequest]:
    store = Store(root / 'jobs.sqlite3')
    controller = Controller(store, OMV())
    controller.archives = Archives(store, root / 'cache')
    inspection = str(uuid4())
    store.create(
        wire.StartOperationRequest(
            request_id=inspection,
            operation=wire.OPERATION_INSPECT_ARCHIVE,
            inspect_archive=wire.ArchiveReference(
                instance_id=str(uuid4()), set_id=str(uuid4()), name='system-20261006T1200Z.tar.zst'
            ),
        ),
        'admin',
    )
    store.update(inspection, wire.JOB_STATE_SUCCEEDED, 'Completed')
    directory = controller.archives.directory(inspection)
    directory.mkdir(parents=True, mode=0o700)
    with tarfile.open(directory / 'archive.tar', 'w') as archive:
        member = tarfile.TarInfo('file')
        member.uid, member.gid, member.size = os.geteuid(), os.getegid(), 7
        archive.addfile(member, io.BytesIO(b'payload'))
    request = wire.StartOperationRequest(
        request_id=str(uuid4()),
        operation=wire.OPERATION_EXTRACT_FILES,
        extract_files=wire.FileExtraction(inspection_id=inspection, paths=['file'], destination=str(root / 'output')),
    )
    return controller, request


def test_final_checkpoint_failure_reconciles_published_files(tmp_path: Path) -> None:
    controller, request = setup(tmp_path)
    config, _ = configuration()
    config['minimumfreebytes'] = 0
    checkpoint = controller.store.checkpoint

    def fail_final_checkpoint(identifier: str, publication: Publication) -> None:
        if publication.phase == 'published':
            raise OSError(errno.ENOSPC, 'Injected final journal write failure')
        checkpoint(identifier, publication)

    try:
        with (
            patch('protondrive_api.archives.STATE', tmp_path),
            patch('protondrive_api.archives.load', return_value=config),
            patch.object(controller.store, 'checkpoint', side_effect=fail_final_checkpoint),
        ):
            controller.start('admin', request)
            controller.jobs.shutdown(wait=True)
        assert (tmp_path / 'output/file').read_bytes() == b'payload'
        assert controller.store.get(request.request_id).state == wire.JOB_STATE_SUCCEEDED
        assert controller.store.publication(request.request_id) is None
    finally:
        controller.close()
    reopened = Store(tmp_path / 'jobs.sqlite3')
    try:
        Archives(reopened, tmp_path / 'cache').recover()
        assert reopened.get(request.request_id).state == wire.JOB_STATE_SUCCEEDED
        assert (tmp_path / 'output/file').read_bytes() == b'payload'
    finally:
        reopened.close()


def test_cancel_cleanup_failure_remains_visible_and_retries_after_restart(tmp_path: Path) -> None:
    controller, request = setup(tmp_path)
    config, _ = configuration()
    config['minimumfreebytes'] = 0
    checkpoint = controller.store.checkpoint

    def cancel_after_staging(identifier: str, publication: Publication) -> None:
        checkpoint(identifier, publication)
        if publication.phase == 'extracting':
            assert controller.controls[identifier].cancel()

    try:
        with (
            patch('protondrive_api.archives.STATE', tmp_path),
            patch('protondrive_api.archives.load', return_value=config),
            patch.object(controller.store, 'checkpoint', side_effect=cancel_after_staging),
            patch('protondrive.restore.shutil.rmtree', side_effect=PermissionError('Injected cleanup failure')),
        ):
            controller.start('admin', request)
            controller.jobs.shutdown(wait=True)
        result = controller.store.get(request.request_id)
        assert result.state == wire.JOB_STATE_INTERRUPTED
        assert 'Recovery requires attention' in result.message
        assert 'Injected cleanup failure' in result.message
        assert controller.store.publication(request.request_id) is not None
        assert len(list(tmp_path.glob('.protondrive-extract-*'))) == 1
    finally:
        controller.close()
    reopened = Store(tmp_path / 'jobs.sqlite3')
    try:
        Archives(reopened, tmp_path / 'cache').recover()
        assert not list(tmp_path.glob('.protondrive-extract-*'))
        assert reopened.get(request.request_id).state == wire.JOB_STATE_INTERRUPTED
        assert reopened.get(request.request_id).message == 'Interrupted extraction cleaned up'
        assert reopened.publication(request.request_id) is None
        sequence = reopened.get(request.request_id).sequence
        Archives(reopened, tmp_path / 'cache').recover()
        assert reopened.get(request.request_id).sequence == sequence
    finally:
        reopened.close()


@pytest.mark.parametrize('state', [wire.JOB_STATE_FAILED, wire.JOB_STATE_CANCELLED, wire.JOB_STATE_INTERRUPTED])
def test_unresolved_terminal_publication_survives_repeated_recovery(
    tmp_path: Path, state: wire.JobState.ValueType
) -> None:
    controller, request = setup(tmp_path)
    config, _ = configuration()
    config['minimumfreebytes'] = 0
    checkpoint = controller.store.checkpoint

    def fail_final_checkpoint(identifier: str, publication: Publication) -> None:
        if publication.phase == 'published':
            raise OSError(errno.ENOSPC, 'Injected final journal write failure')
        checkpoint(identifier, publication)

    try:
        with (
            patch('protondrive_api.archives.load', return_value=config),
            patch.object(controller.store, 'checkpoint', side_effect=fail_final_checkpoint),
            patch('protondrive_api.archives.reconcile', side_effect=OSError('Filesystem temporarily unavailable')),
        ):
            controller.start('admin', request)
            controller.jobs.shutdown(wait=True)
        assert controller.store.get(request.request_id).state == wire.JOB_STATE_INTERRUPTED
        assert controller.store.publication(request.request_id) is not None
        # Cover journals left behind by the previous implementation, too.
        controller.store.update(request.request_id, state, 'Unresolved publication')
    finally:
        controller.close()

    reopened = Store(tmp_path / 'jobs.sqlite3')
    try:
        with patch('protondrive_api.archives.reconcile', side_effect=OSError('Filesystem still unavailable')):
            Archives(reopened, tmp_path / 'cache').recover()
        assert reopened.get(request.request_id).state == wire.JOB_STATE_INTERRUPTED
        assert 'Filesystem still unavailable' in reopened.get(request.request_id).message
        assert reopened.publication(request.request_id) is not None
    finally:
        reopened.close()

    recovered = Store(tmp_path / 'jobs.sqlite3')
    try:
        Archives(recovered, tmp_path / 'cache').recover()
        assert recovered.get(request.request_id).state == wire.JOB_STATE_SUCCEEDED
        assert (tmp_path / 'output/file').read_bytes() == b'payload'
        assert recovered.publication(request.request_id) is None
        sequence = recovered.get(request.request_id).sequence
        Archives(recovered, tmp_path / 'cache').recover()
        assert recovered.get(request.request_id).sequence == sequence
    finally:
        recovered.close()


def test_clean_cancellation_retires_its_journal(tmp_path: Path) -> None:
    controller, request = setup(tmp_path)
    config, _ = configuration()
    config['minimumfreebytes'] = 0
    checkpoint = controller.store.checkpoint

    def cancel_after_staging(identifier: str, publication: Publication) -> None:
        checkpoint(identifier, publication)
        if publication.phase == 'extracting':
            assert controller.controls[identifier].cancel()

    try:
        with (
            patch('protondrive_api.archives.load', return_value=config),
            patch.object(controller.store, 'checkpoint', side_effect=cancel_after_staging),
        ):
            controller.start('admin', request)
            controller.jobs.shutdown(wait=True)
        assert controller.store.get(request.request_id).state == wire.JOB_STATE_CANCELLED
        assert not (tmp_path / 'output').exists()
        assert not list(tmp_path.glob('.protondrive-extract-*'))
        assert controller.store.publication(request.request_id) is None
        assert not controller.store.recovery_jobs()
    finally:
        controller.close()
