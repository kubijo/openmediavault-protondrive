"""Exercise durable admission, replay and controller restart semantics."""

from pathlib import Path
from uuid import uuid4

import pytest

from protondrive_api.store import Store
from protondrive_api.v1 import control_pb2 as wire


def request(operation: wire.Operation.ValueType = wire.OPERATION_BACKUP) -> wire.StartOperationRequest:
    return wire.StartOperationRequest(operation=operation, request_id=str(uuid4()))


def test_idempotent_admission_and_replay_survive_reopening(tmp_path: Path) -> None:
    path = tmp_path / 'jobs.sqlite3'
    store = Store(path)
    item = request()
    job, created = store.create(item, 'admin')
    assert created
    assert store.create(item, 'admin') == (job, False)
    store.update(job.id, wire.JOB_STATE_RUNNING, 'Downloading')
    store.update(job.id, wire.JOB_STATE_SUCCEEDED, 'Verified')
    store.close()
    reopened = Store(path)
    assert [event.message for event in reopened.events(job.id, 1)] == ['Downloading', 'Verified']
    assert reopened.get(job.id).sequence == 3
    assert path.stat().st_mode & 0o777 == 0o600
    reopened.close()


def test_busy_admission_still_allows_cancellation(tmp_path: Path) -> None:
    store = Store(tmp_path / 'jobs.sqlite3')
    store.create(request(), 'admin')
    with pytest.raises(ValueError, match='Another operation'):
        store.create(request(), 'admin')
    _, created = store.create(request(wire.OPERATION_CANCEL_BACKUP), 'admin')
    assert created
    with pytest.raises(ValueError, match='Another operation'):
        store.create(request(wire.OPERATION_CANCEL_BACKUP), 'admin')
    store.close()


def test_restart_does_not_replay_side_effects(tmp_path: Path) -> None:
    store = Store(tmp_path / 'jobs.sqlite3')
    job, _ = store.create(request(), 'admin')
    store.interrupt_abandoned()
    assert store.get(job.id).state == wire.JOB_STATE_INTERRUPTED
    assert len(store.events(job.id, 0)) == 2
    store.interrupt_abandoned()
    assert len(store.events(job.id, 0)) == 2
    store.close()


def test_request_id_cannot_change_actor_or_operation(tmp_path: Path) -> None:
    store = Store(tmp_path / 'jobs.sqlite3')
    item = request()
    store.create(item, 'admin')
    with pytest.raises(ValueError, match='different operation'):
        store.create(item, 'another-admin')
    item.operation = wire.OPERATION_SIGN_OUT
    with pytest.raises(ValueError, match='different operation'):
        store.create(item, 'admin')
    store.close()


def test_refuses_symlink_database(tmp_path: Path) -> None:
    path = tmp_path / 'jobs.sqlite3'
    path.symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='symlink'):
        Store(path)
