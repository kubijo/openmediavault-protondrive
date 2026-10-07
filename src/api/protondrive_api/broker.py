"""Privileged adapter and durable job admission, isolated from HTTP handling."""

import logging
import os
import pwd
import re
import socket
import socketserver
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import protovalidate

from protondrive.common import STATE, locked, request
from protondrive.containers import inventory
from protondrive.operation import Cancelled, OperationControl

from .archives import Archives, browse
from .omv import OMV
from .store import Store
from .transport import SOCKET, read_frame, write_frame
from .v1 import control_pb2 as wire

logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, store: Store, omv: OMV) -> None:
        self.store = store
        self.omv = omv
        self.archives = Archives(store)
        self.admission = threading.Lock()
        self.jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix='protondrive-job')
        self.controls: dict[str, OperationControl] = {}

    def start(self, actor: str, request: wire.StartOperationRequest) -> wire.StartOperationResponse:
        job, created = self.store.create(request, actor)
        if created:
            self.controls[job.id] = OperationControl()
            self.jobs.submit(self._run, actor, job)
        return wire.StartOperationResponse(job=job)

    def _run(self, actor: str, job: wire.Job) -> None:
        last = ''
        control = self.controls[job.id]

        def progress(detail: str) -> None:
            nonlocal last
            if detail != last:
                self.store.update(job.id, wire.JOB_STATE_RUNNING, detail)
                last = detail

        try:
            progress('Started')
            parameters = self.store.parameters(job.id)
            if job.operation == wire.OPERATION_INSPECT_ARCHIVE:
                self.archives.inspect(job.id, parameters.inspect_archive, progress, control)
            elif job.operation == wire.OPERATION_EXTRACT_FILES:
                self.archives.extract(job.id, parameters.extract_files, progress, control)
            else:
                self.omv.operation(actor, job.operation, progress)
        except Cancelled:
            if job.operation == wire.OPERATION_EXTRACT_FILES:
                self.archives.resolve_extraction(job.id, wire.JOB_STATE_CANCELLED, 'Cancelled; no files published')
            else:
                self.store.update(job.id, wire.JOB_STATE_CANCELLED, 'Cancelled; no files published')
        except Exception as exc:
            logger.exception('Operation %s failed', job.id)
            if job.operation == wire.OPERATION_EXTRACT_FILES:
                self.archives.resolve_extraction(job.id, wire.JOB_STATE_FAILED, str(exc))
            else:
                try:
                    control.check()
                except Cancelled:
                    self.store.update(job.id, wire.JOB_STATE_CANCELLED, 'Cancelled; no files published')
                else:
                    self.store.update(job.id, wire.JOB_STATE_FAILED, str(exc))
        else:
            self.store.update(
                job.id,
                wire.JOB_STATE_SUCCEEDED,
                'Completed',
                clear_publication=job.operation == wire.OPERATION_EXTRACT_FILES,
            )
        finally:
            with self.admission:
                self.controls.pop(job.id, None)

    def cancel(self, identifier: str) -> wire.CancelJobResponse:
        job = self.store.get(identifier)
        if job.operation not in (wire.OPERATION_INSPECT_ARCHIVE, wire.OPERATION_EXTRACT_FILES):
            raise ValueError('This operation uses its dedicated cancellation control')
        if job.state not in (wire.JOB_STATE_QUEUED, wire.JOB_STATE_RUNNING):
            return wire.CancelJobResponse(job=job)
        control = self.controls.get(identifier)
        if control is None or not control.cancel():
            raise ValueError('Publication has started; wait for the operation result')
        if job.operation == wire.OPERATION_INSPECT_ARCHIVE:
            request('cancel-download', timeout=5, jobuuid=identifier)
        return wire.CancelJobResponse(job=job)

    def dispatch(self, request: wire.BrokerRequest) -> wire.BrokerResponse:
        protovalidate.validate(request)
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]{0,127}', request.actor):
            raise ValueError('Invalid authenticated actor')
        if request.WhichOneof('request') == 'backups':
            return wire.BrokerResponse(backups=browse(request.backups))
        if request.WhichOneof('request') == 'archive':
            return wire.BrokerResponse(archive=self.archives.get(request.archive))
        with self.admission:
            match request.WhichOneof('request'):
                case 'status':
                    return wire.BrokerResponse(status=self.omv.status(request.actor))
                case 'configuration':
                    return wire.BrokerResponse(configuration=self.omv.get_configuration(request.actor))
                case 'save_configuration':
                    return wire.BrokerResponse(
                        save_configuration=self.omv.save_configuration(request.actor, request.save_configuration)
                    )
                case 'sets':
                    return wire.BrokerResponse(sets=self.omv.list_sets(request.actor))
                case 'save_set':
                    return wire.BrokerResponse(save_set=self.omv.save_set(request.actor, request.save_set))
                case 'delete_set':
                    return wire.BrokerResponse(delete_set=self.omv.delete_set(request.actor, request.delete_set))
                case 'start_operation':
                    return wire.BrokerResponse(start_operation=self.start(request.actor, request.start_operation))
                case 'cancel_job':
                    return wire.BrokerResponse(cancel_job=self.cancel(request.cancel_job.id))
                case 'preview_extraction':
                    return wire.BrokerResponse(
                        preview_extraction=self.archives.preview(request.preview_extraction.extraction)
                    )
                case 'release_archive':
                    self.archives.release(request.release_archive.inspection_id)
                    return wire.BrokerResponse(release_archive=wire.ReleaseArchiveResponse())
                case 'list_archives':
                    return wire.BrokerResponse(list_archives=self.archives.list())
                case 'job':
                    return wire.BrokerResponse(job=wire.GetJobResponse(job=self.store.get(request.job.id)))
                case 'events':
                    return wire.BrokerResponse(
                        events=wire.JobEvents(jobs=self.store.events(request.events.id, request.events.after_sequence))
                    )
                case 'directories':
                    return wire.BrokerResponse(directories=self.omv.browse(request.actor, request.directories))
                case 'containers':
                    return wire.BrokerResponse(
                        containers=wire.ListContainersResponse(
                            containers=[
                                wire.Container(
                                    id=item.identifier,
                                    name=item.name,
                                    compose_project=item.project,
                                    running=item.running,
                                )
                                for item in inventory()
                            ]
                        )
                    )
                case _:
                    raise ValueError('Unknown controller operation')

    def close(self) -> None:
        self.jobs.shutdown(wait=True)
        self.store.close()


def serve(path: Path = SOCKET) -> None:
    if os.geteuid() != 0:
        raise PermissionError('The controller requires root')
    os.umask(0o077)
    account = pwd.getpwnam('protondrive-api')
    with locked(STATE / 'controller.lock'):
        store = Store(STATE / 'jobs.sqlite3')
        controller = Controller(store, OMV())
        controller.archives.recover()
        store.interrupt_abandoned()

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                connection = cast(object, self.request)
                if not isinstance(connection, socket.socket):
                    raise TypeError('Expected a Unix socket')
                connection.settimeout(35)
                credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                uid = int.from_bytes(credentials[4:8], sys.byteorder, signed=True)
                if uid not in (0, account.pw_uid):
                    return
                try:
                    response = controller.dispatch(wire.BrokerRequest.FromString(read_frame(connection)))
                except (ValueError, protovalidate.ValidationError) as exc:
                    response = wire.BrokerResponse(error=wire.BrokerError(code='invalid_argument', message=str(exc)))
                except LookupError:
                    response = wire.BrokerResponse(error=wire.BrokerError(code='not_found', message='Job not found'))
                except Exception:
                    logger.exception('Controller operation failed')
                    response = wire.BrokerResponse(
                        error=wire.BrokerError(
                            code='internal', message='Controller operation failed; inspect the service journal'
                        )
                    )
                write_frame(connection, response.SerializeToString())

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        os.chown(path.parent, 0, account.pw_gid)
        path.unlink(missing_ok=True)
        try:
            with Server(str(path), Handler) as server:
                path.chmod(0o660)
                os.chown(path, 0, account.pw_gid)
                server.serve_forever()
        finally:
            controller.close()


if __name__ == '__main__':
    serve()
