"""Archive admission and unprivileged download boundary regression checks."""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from helpers import configuration
from protondrive.common import BackupError
from protondrive.json_data import JSONValue
from protondrive.models import Manifest
from protondrive.operation import OperationControl
from protondrive.restore_journal import OWNER_FILE, Publication
from protondrive_api.archives import Archives, copy_download
from protondrive_api.broker import Controller
from protondrive_api.omv import OMV
from protondrive_api.store import Store
from protondrive_api.v1 import control_pb2 as wire


@pytest.fixture(autouse=True)
def private_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('protondrive_api.archives.STATE', tmp_path)


def test_expected_remote_failure_is_actionable_at_the_broker_boundary(tmp_path: Path) -> None:
    controller = Controller(Store(tmp_path / 'jobs.sqlite3'), OMV())
    try:
        with patch('protondrive_api.archives.request', side_effect=BackupError('Proton service is busy', code='busy')):
            response = controller.respond(wire.BrokerRequest(actor='admin', backups=wire.BrowseBackupsRequest()))
        assert response.error.code == 'busy'
        assert response.error.message == 'Proton service is busy'
    finally:
        controller.close()


def test_download_discard_retries_by_busy_code_not_message() -> None:
    with patch(
        'protondrive_api.archives.request',
        side_effect=[BackupError('New busy wording', code='busy'), True],
    ) as remote:
        Archives.discard_download(str(uuid4()))
    assert remote.call_count == 2


@pytest.mark.parametrize('corrupt,compose', [(False, False), (False, True), (True, False)])
def test_inspection_to_extraction_checks_real_archive_bytes(tmp_path: Path, corrupt: bool, compose: bool) -> None:
    """Only the daemon download is faked; checksum, zstd, tar and journal are real."""
    config, item = configuration()
    config['minimumfreebytes'] = 0
    source = tmp_path / 'source.tar'
    with tarfile.open(source, 'w') as archive:
        for name in ['selected.txt', 'unselected.txt']:
            member = tarfile.TarInfo(name)
            member.uid, member.gid = os.geteuid(), os.getegid()
            member.size = len(name)
            archive.addfile(member, io.BytesIO(name.encode()))
        if compose:
            for name in ['srv/app/compose.yaml', 'srv/app/.env']:
                member = tarfile.TarInfo(name)
                member.size = 1
                archive.addfile(member, io.BytesIO(b'x'))
            directory = tarfile.TarInfo('srv/app/data')
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
    packed = subprocess.run(['zstd', '-c', '--', str(source)], check=True, capture_output=True).stdout
    request = wire.StartOperationRequest(
        request_id=str(uuid4()),
        operation=wire.OPERATION_INSPECT_ARCHIVE,
        inspect_archive=wire.ArchiveReference(
            instance_id=str(uuid4()), set_id=item['uuid'], name='system-20261006T1200Z.tar.zst'
        ),
    )
    downloaded = tmp_path / 'downloads'
    folder = downloaded / request.request_id
    folder.mkdir(parents=True)
    (folder / request.inspect_archive.name).write_bytes(packed)
    manifest: Manifest = {
        'format': 2 if compose else 1,
        'instanceuuid': request.inspect_archive.instance_id,
        'setuuid': item['uuid'],
        'archive': request.inspect_archive.name,
        'timestamp': '20261006T1200Z',
        'size': len(packed),
        'sha256': '0' * 64 if corrupt else hashlib.sha256(packed).hexdigest(),
    }
    if compose:
        manifest['compose'] = [
            {
                'project': 'app',
                'definitions': ['/srv/app/compose.yaml'],
                'envfiles': ['/srv/app/.env'],
                'secretfiles': [],
                'services': [
                    {
                        'service': 'web',
                        'image': 'nginx:stable',
                        'pinned': 'docker.io/library/nginx@sha256:' + 'a' * 64,
                        'replicas': 2,
                        'binds': [{'source': '/srv/app/data', 'target': '/data', 'read_only': False}],
                    }
                ],
            }
        ]
    calls: list[str] = []

    def remote(operation: str, **_parameters: object) -> Manifest | dict[str, JSONValue]:
        calls.append(operation)
        if operation == 'download-archive':
            return manifest
        assert operation == 'discard-download'
        shutil.rmtree(folder)
        return {}

    store = Store(tmp_path / 'jobs.sqlite3')
    controller = Archives(store, tmp_path / 'cache')
    store.create(request, 'admin')
    try:
        with (
            patch('protondrive_api.archives.STATE', tmp_path),
            patch('protondrive_api.archives.DOWNLOAD_CACHE', downloaded),
            patch('protondrive_api.archives.load', return_value=config),
            patch('protondrive_api.archives.request', side_effect=remote),
        ):
            if corrupt:
                with pytest.raises(BackupError, match='checksum'):
                    controller.inspect(request.request_id, request.inspect_archive, lambda _: None, OperationControl())
                assert not controller.directory(request.request_id).exists()
                return
            controller.inspect(request.request_id, request.inspect_archive, lambda _: None, OperationControl())
            store.update(request.request_id, wire.JOB_STATE_SUCCEEDED, 'Completed')
            assert controller.list().archives[0].source == request.inspect_archive
            inspected = controller.get(wire.GetArchiveRequest(inspection_id=request.request_id))
            assert inspected.total == (5 if compose else 2)
            assert len(inspected.compose) == int(compose)
            if compose:
                assert inspected.compose[0].services[0].replicas == 2
            extraction = wire.FileExtraction(
                inspection_id=request.request_id, paths=['selected.txt'], destination=str(tmp_path / 'output')
            )
            assert controller.preview(extraction).entries == 1
            extract_id = str(uuid4())
            store.create(
                wire.StartOperationRequest(
                    request_id=extract_id, operation=wire.OPERATION_EXTRACT_FILES, extract_files=extraction
                ),
                'admin',
            )
            with pytest.raises(ValueError, match='current operation'):
                controller.release(request.request_id)
            controller.extract(extract_id, extraction, lambda _: None, OperationControl())
            store.update(extract_id, wire.JOB_STATE_SUCCEEDED, 'Completed')
            assert (tmp_path / 'output/selected.txt').read_text() == 'selected.txt'
            assert not (tmp_path / 'output/unselected.txt').exists()
            with pytest.raises(ValueError, match='already exists'):
                controller.preview(extraction)
            controller.release(request.request_id)
            assert not controller.list().archives
            with pytest.raises(ValueError, match='released'):
                controller.get(wire.GetArchiveRequest(inspection_id=request.request_id))
    finally:
        store.close()
        assert calls == ['download-archive', 'discard-download']
        assert not folder.exists()


def test_download_copy_rejects_symlinks_at_every_level_and_size_changes(tmp_path: Path) -> None:
    folder = tmp_path / 'download'
    folder.mkdir()
    source = folder / 'archive'
    source.write_bytes(b'archive')
    alias = tmp_path / 'alias'
    alias.symlink_to(folder, target_is_directory=True)
    target = tmp_path / 'private'
    with pytest.raises(OSError):
        copy_download(alias / 'archive', target, 7)
    with pytest.raises(BackupError, match='declared size'):
        copy_download(source, target, 6)
    link = folder / 'link'
    link.symlink_to(source)
    with pytest.raises(OSError):
        copy_download(link, target, 7)
    copy_download(source, target, 7)
    assert target.read_bytes() == b'archive'
    assert target.stat().st_mode & 0o777 == 0o600


def test_parameters_are_durable_and_cannot_change_on_retry(tmp_path: Path) -> None:
    path = tmp_path / 'jobs.sqlite3'
    store = Store(path)
    request = wire.StartOperationRequest(
        request_id=str(uuid4()),
        operation=wire.OPERATION_INSPECT_ARCHIVE,
        inspect_archive=wire.ArchiveReference(
            instance_id=str(uuid4()), set_id=str(uuid4()), name='data-20261006T1200Z.tar.zst'
        ),
    )
    store.create(request, 'admin')
    store.close()
    store = Store(path)
    assert store.parameters(request.request_id) == request
    store.create(request, 'admin')
    request.inspect_archive.name = 'different-20261006T1200Z.tar.zst'
    with pytest.raises(ValueError, match='different operation'):
        store.create(request, 'admin')
    store.close()


def test_invalid_payload_and_incomplete_inspection_cannot_extract(tmp_path: Path) -> None:
    store = Store(tmp_path / 'jobs.sqlite3')
    request = wire.StartOperationRequest(request_id=str(uuid4()), operation=wire.OPERATION_INSPECT_ARCHIVE)
    with pytest.raises(ValueError, match='parameters'):
        store.create(request, 'admin')
    request.operation = wire.OPERATION_BACKUP
    store.create(request, 'admin')
    with pytest.raises(ValueError, match='inspection'):
        Archives(store, tmp_path).get(wire.GetArchiveRequest(inspection_id=request.request_id))
    store.close()


def test_restart_reconciles_published_extraction_without_replaying_it(tmp_path: Path) -> None:
    store = Store(tmp_path / 'jobs.sqlite3')
    job = str(uuid4())
    store.create(
        wire.StartOperationRequest(
            request_id=job,
            operation=wire.OPERATION_EXTRACT_FILES,
            extract_files=wire.FileExtraction(
                inspection_id=str(uuid4()), paths=['file'], destination=str(tmp_path / 'output')
            ),
        ),
        'admin',
    )
    destination = tmp_path / 'output'
    destination.mkdir()
    marker = destination / OWNER_FILE
    marker.write_bytes(b'staging')
    marker.chmod(0o600)
    (destination / 'file').write_text('published')
    parent, info = tmp_path.stat(), destination.stat()
    checkpoint = Publication(
        str(tmp_path), parent.st_dev, parent.st_ino, 'staging', 'output', info.st_dev, info.st_ino, 'publishing'
    )
    store.checkpoint(job, checkpoint)
    store.close()
    store = Store(tmp_path / 'jobs.sqlite3')
    assert store.publication(job) == checkpoint
    Archives(store, tmp_path / 'cache').recover()
    assert store.get(job).state == wire.JOB_STATE_SUCCEEDED
    assert (destination / 'file').read_text() == 'published'
    store.close()
