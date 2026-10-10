"""Generated Connect service backed by the private controller protocol."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import TypeVar

import protovalidate
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from protobuf.wkt import Any

from .transport import BrokerClient
from .v1 import control_pb2 as wire
from .v1.control_connect import ControlServiceASGIApplication

Request = TypeVar('Request')
Response = TypeVar('Response')


class Control:
    def __init__(self, broker: BrokerClient) -> None:
        self.broker = broker

    async def list_archives(
        self,
        request: wire.ListArchivesRequest,
        ctx: RequestContext[wire.ListArchivesRequest, wire.ListArchivesResponse],
    ) -> wire.ListArchivesResponse:
        return (await self.call(wire.BrokerRequest(list_archives=request), ctx)).list_archives

    async def cancel_job(
        self,
        request: wire.CancelJobRequest,
        ctx: RequestContext[wire.CancelJobRequest, wire.CancelJobResponse],
    ) -> wire.CancelJobResponse:
        return (await self.call(wire.BrokerRequest(cancel_job=request), ctx)).cancel_job

    async def preview_extraction(
        self,
        request: wire.PreviewExtractionRequest,
        ctx: RequestContext[wire.PreviewExtractionRequest, wire.PreviewExtractionResponse],
    ) -> wire.PreviewExtractionResponse:
        return (await self.call(wire.BrokerRequest(preview_extraction=request), ctx)).preview_extraction

    async def release_archive(
        self,
        request: wire.ReleaseArchiveRequest,
        ctx: RequestContext[wire.ReleaseArchiveRequest, wire.ReleaseArchiveResponse],
    ) -> wire.ReleaseArchiveResponse:
        return (await self.call(wire.BrokerRequest(release_archive=request), ctx)).release_archive

    async def get_archive(
        self, request: wire.GetArchiveRequest, ctx: RequestContext[wire.GetArchiveRequest, wire.GetArchiveResponse]
    ) -> wire.GetArchiveResponse:
        return (await self.call(wire.BrokerRequest(archive=request), ctx)).archive

    async def browse_backups(
        self,
        request: wire.BrowseBackupsRequest,
        ctx: RequestContext[wire.BrowseBackupsRequest, wire.BrowseBackupsResponse],
    ) -> wire.BrowseBackupsResponse:
        return (await self.call(wire.BrokerRequest(backups=request), ctx)).backups

    async def list_containers(
        self,
        request: wire.ListContainersRequest,
        ctx: RequestContext[wire.ListContainersRequest, wire.ListContainersResponse],
    ) -> wire.ListContainersResponse:
        return (await self.call(wire.BrokerRequest(containers=request), ctx)).containers

    async def call(self, request: wire.BrokerRequest, ctx: RequestContext[Request, Response]) -> wire.BrokerResponse:
        actor = ctx.request_headers.get('x-protondrive-user', '')
        if ctx.request_headers.get('x-protondrive-authenticated') != '1' or not re.fullmatch(
            r'[a-zA-Z_][a-zA-Z0-9_.-]{0,127}',
            actor,
        ):
            raise ConnectError(Code.UNAUTHENTICATED, 'An OMV administrator session is required')
        request.actor = actor
        try:
            protovalidate.validate(request)
        except protovalidate.ValidationError as exc:
            raise ConnectError(Code.INVALID_ARGUMENT, str(exc)) from exc
        try:
            result = await asyncio.to_thread(self.broker.call, request)
        except (OSError, ValueError) as exc:
            raise ConnectError(Code.UNAVAILABLE, 'The controller is unavailable') from exc
        if result.HasField('error'):
            codes = {
                'invalid_argument': Code.INVALID_ARGUMENT,
                'not_found': Code.NOT_FOUND,
                'failed_precondition': Code.FAILED_PRECONDITION,
                'sign_in_required': Code.FAILED_PRECONDITION,
                'unavailable': Code.UNAVAILABLE,
                'busy': Code.RESOURCE_EXHAUSTED,
                'invalid_archive': Code.DATA_LOSS,
                'internal': Code.INTERNAL,
            }
            code = codes.get(result.error.code)
            if code is None:
                raise ConnectError(Code.INTERNAL, 'The controller returned an invalid error')
            detail = wire.FailureDetail(code=result.error.code)
            raise ConnectError(
                code,
                result.error.message,
                details=[
                    Any(type_url=f'type.googleapis.com/{detail.DESCRIPTOR.full_name}', value=detail.SerializeToString())
                ],
            )
        if result.WhichOneof('response') != request.WhichOneof('request'):
            raise ConnectError(Code.INTERNAL, 'The controller returned an invalid response')
        return result

    async def get_status(
        self, request: wire.GetStatusRequest, ctx: RequestContext[wire.GetStatusRequest, wire.GetStatusResponse]
    ) -> wire.GetStatusResponse:
        return (await self.call(wire.BrokerRequest(status=request), ctx)).status

    async def get_configuration(
        self,
        request: wire.GetConfigurationRequest,
        ctx: RequestContext[wire.GetConfigurationRequest, wire.GetConfigurationResponse],
    ) -> wire.GetConfigurationResponse:
        return (await self.call(wire.BrokerRequest(configuration=request), ctx)).configuration

    async def save_configuration(
        self,
        request: wire.SaveConfigurationRequest,
        ctx: RequestContext[wire.SaveConfigurationRequest, wire.SaveConfigurationResponse],
    ) -> wire.SaveConfigurationResponse:
        return (await self.call(wire.BrokerRequest(save_configuration=request), ctx)).save_configuration

    async def list_sets(
        self, request: wire.ListSetsRequest, ctx: RequestContext[wire.ListSetsRequest, wire.ListSetsResponse]
    ) -> wire.ListSetsResponse:
        return (await self.call(wire.BrokerRequest(sets=request), ctx)).sets

    async def save_set(
        self, request: wire.SaveSetRequest, ctx: RequestContext[wire.SaveSetRequest, wire.SaveSetResponse]
    ) -> wire.SaveSetResponse:
        return (await self.call(wire.BrokerRequest(save_set=request), ctx)).save_set

    async def delete_set(
        self, request: wire.DeleteSetRequest, ctx: RequestContext[wire.DeleteSetRequest, wire.DeleteSetResponse]
    ) -> wire.DeleteSetResponse:
        return (await self.call(wire.BrokerRequest(delete_set=request), ctx)).delete_set

    async def start_operation(
        self,
        request: wire.StartOperationRequest,
        ctx: RequestContext[wire.StartOperationRequest, wire.StartOperationResponse],
    ) -> wire.StartOperationResponse:
        return (await self.call(wire.BrokerRequest(start_operation=request), ctx)).start_operation

    async def get_job(
        self, request: wire.GetJobRequest, ctx: RequestContext[wire.GetJobRequest, wire.GetJobResponse]
    ) -> wire.GetJobResponse:
        return (await self.call(wire.BrokerRequest(job=request), ctx)).job

    async def watch_job(
        self,
        request: wire.WatchJobRequest,
        ctx: RequestContext[wire.WatchJobRequest, wire.WatchJobResponse],
    ) -> AsyncIterator[wire.WatchJobResponse]:
        # Force periodic authenticated reconnects; an open stream must not outlive
        # an expired OMV session indefinitely. The replay cursor prevents gaps.
        deadline = time.monotonic() + 25
        cursor = wire.WatchJobRequest(id=request.id, after_sequence=request.after_sequence)
        while time.monotonic() < deadline:
            response = await self.call(wire.BrokerRequest(events=cursor), ctx)
            for job in response.events.jobs:
                yield wire.WatchJobResponse(job=job)
                cursor.after_sequence = job.sequence
                if job.state in (
                    wire.JOB_STATE_SUCCEEDED,
                    wire.JOB_STATE_FAILED,
                    wire.JOB_STATE_INTERRUPTED,
                    wire.JOB_STATE_CANCELLED,
                ):
                    return
            await asyncio.sleep(0.5)

    async def browse_directories(
        self,
        request: wire.BrowseDirectoriesRequest,
        ctx: RequestContext[wire.BrowseDirectoriesRequest, wire.BrowseDirectoriesResponse],
    ) -> wire.BrowseDirectoriesResponse:
        return (await self.call(wire.BrokerRequest(directories=request), ctx)).directories


app = ControlServiceASGIApplication(Control(BrokerClient()))
