"""Controller failures retain a useful public result without exposing diagnostics."""

import logging
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from protondrive.common import BackupError
from protondrive_api.broker import Controller
from protondrive_api.omv import OMV
from protondrive_api.store import Store
from protondrive_api.v1 import control_pb2 as wire


def controller_at(path: Path) -> Controller:
    return Controller(Store(path / 'jobs.sqlite3'), OMV())


def test_sync_failure_code_reaches_broker_response(tmp_path: Path) -> None:
    controller = controller_at(tmp_path)
    try:
        with patch.object(
            controller, 'dispatch', side_effect=BackupError('Sign in to Proton Drive', code='sign_in_required')
        ):
            result = controller.respond(wire.BrokerRequest())
        assert result.error.code == 'sign_in_required'
        assert result.error.message == 'Sign in to Proton Drive'
    finally:
        controller.close()


def test_unexpected_sync_failure_has_journal_reference(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    controller = controller_at(tmp_path)
    try:
        with (
            caplog.at_level(logging.ERROR),
            patch.object(controller, 'dispatch', side_effect=RuntimeError('private vendor diagnostic')),
        ):
            result = controller.respond(wire.BrokerRequest())
        assert result.error.code == 'internal'
        reference = result.error.message.rsplit(' ', 1)[-1]
        assert len(reference) == 12
        assert reference in caplog.text
        assert 'private vendor diagnostic' not in result.error.message
    finally:
        controller.close()


def test_unexpected_background_failure_uses_job_id_as_reference(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    controller = controller_at(tmp_path)
    try:
        request = wire.StartOperationRequest(request_id=str(uuid4()), operation=wire.OPERATION_BACKUP)
        with (
            caplog.at_level(logging.ERROR),
            patch.object(controller.omv, 'operation', side_effect=RuntimeError('private vendor diagnostic')),
        ):
            job = controller.start('admin', request).job
            controller.jobs.shutdown(wait=True)
        result = controller.store.get(job.id)
        assert result.state == wire.JOB_STATE_FAILED
        assert result.failure_code == 'internal'
        assert job.id in result.message
        assert 'private vendor diagnostic' not in result.message
        assert job.id in caplog.text
    finally:
        controller.close()
