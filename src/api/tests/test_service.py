"""Exercise generated Connect routing, validation and authentication boundaries."""

import base64
from typing import cast

import httpx
import pytest

from protondrive_api.service import Control
from protondrive_api.transport import BrokerClient
from protondrive_api.v1 import control_pb2 as wire
from protondrive_api.v1.control_connect import ControlServiceASGIApplication


class RecordingBroker(BrokerClient):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[wire.BrokerRequest] = []

    def call(self, request: wire.BrokerRequest) -> wire.BrokerResponse:
        self.requests.append(request)
        return wire.BrokerResponse(status=wire.GetStatusResponse(phase='idle'))


class ErrorBroker(RecordingBroker):
    def __init__(self, code: str, message: str) -> None:
        super().__init__()
        self.code = code
        self.message = message

    def call(self, request: wire.BrokerRequest) -> wire.BrokerResponse:
        self.requests.append(request)
        return wire.BrokerResponse(error=wire.BrokerError(code=self.code, message=self.message))


async def test_public_rpc_requires_authenticated_proxy_identity() -> None:
    broker = RecordingBroker()
    app = ControlServiceASGIApplication(Control(broker))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post('/protondrive_api.v1.ControlService/GetStatus', json={})
        assert result.status_code == 401
        assert not broker.requests


async def test_authenticated_status_uses_generated_contract() -> None:
    broker = RecordingBroker()
    app = ControlServiceASGIApplication(Control(broker))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post(
            '/protondrive_api.v1.ControlService/GetStatus',
            json={},
            headers={
                'x-protondrive-authenticated': '1',
                'x-protondrive-user': 'admin',
            },
        )
        assert result.status_code == 200
        assert 'idle' in result.text
        assert broker.requests[0].actor == 'admin'


async def test_invalid_operation_never_reaches_broker() -> None:
    broker = RecordingBroker()
    app = ControlServiceASGIApplication(Control(broker))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post(
            '/protondrive_api.v1.ControlService/StartOperation',
            json={
                'operation': 'OPERATION_BACKUP',
                'requestId': 'not-a-uuid',
            },
            headers={'x-protondrive-authenticated': '1', 'x-protondrive-user': 'admin'},
        )
        assert result.status_code == 400
        assert not broker.requests


async def test_wrong_controller_response_cannot_report_success() -> None:
    app = ControlServiceASGIApplication(Control(RecordingBroker()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post(
            '/protondrive_api.v1.ControlService/GetConfiguration',
            json={},
            headers={
                'x-protondrive-authenticated': '1',
                'x-protondrive-user': 'admin',
            },
        )
        assert result.status_code == 500
        assert 'invalid response' in result.text


@pytest.mark.parametrize(
    ('code', 'status'),
    [
        ('sign_in_required', 400),
        ('unavailable', 503),
        ('busy', 429),
        ('invalid_archive', 500),
        ('failed_precondition', 400),
    ],
)
async def test_actionable_failures_keep_their_connect_status(code: str, status: int) -> None:
    app = ControlServiceASGIApplication(Control(ErrorBroker(code, 'Safe administrator action')))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post(
            '/protondrive_api.v1.ControlService/GetStatus',
            json={},
            headers={'x-protondrive-authenticated': '1', 'x-protondrive-user': 'admin'},
        )
    assert result.status_code == status
    assert 'Safe administrator action' in result.text
    details = cast(list[dict[str, str]], result.json()['details'])
    detail = details[0]
    assert detail['type'] == 'protondrive_api.v1.FailureDetail'
    encoded = detail['value']
    failure = wire.FailureDetail.FromString(base64.b64decode(encoded + '=' * (-len(encoded) % 4)))
    assert failure.code == code


async def test_unknown_broker_error_code_does_not_expose_its_message() -> None:
    app = ControlServiceASGIApplication(Control(ErrorBroker('unknown-code', 'private vendor diagnostic')))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post(
            '/protondrive_api.v1.ControlService/GetStatus',
            json={},
            headers={'x-protondrive-authenticated': '1', 'x-protondrive-user': 'admin'},
        )
    assert result.status_code == 500
    assert 'private vendor diagnostic' not in result.text
