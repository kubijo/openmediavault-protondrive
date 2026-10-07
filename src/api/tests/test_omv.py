"""OMV CLI failures and background jobs must never become successful jobs."""

import subprocess
from unittest.mock import patch

import pytest

from protondrive_api.omv import OMV, new_object_id
from protondrive_api.v1 import control_pb2 as wire


def test_cli_error_envelope_is_not_a_success() -> None:
    with patch('protondrive_api.omv.execute', return_value='{"response":null,"error":{"message":"secret"}}'):
        with pytest.raises(RuntimeError, match='Config.isDirty failed') as error:
            OMV().rpc('admin', 'Config', 'isDirty', {'modules': ['protondrive']})
        assert 'secret' not in str(error.value)


def test_nonzero_cli_redacts_output() -> None:
    failure = subprocess.CompletedProcess(['/usr/sbin/omv-rpc'], 1, stdout='secret', stderr='credential')
    with patch('protondrive_api.omv.subprocess.run', return_value=failure):
        with pytest.raises(RuntimeError, match='exit 1') as error:
            OMV().rpc('admin', 'Config', 'isDirty', {})
        assert 'secret' not in str(error.value)
        assert 'credential' not in str(error.value)


def test_apply_observes_background_task_and_propagates_failure() -> None:
    with patch.object(OMV, 'rpc', side_effect=['/tmp/task', RuntimeError('deployment failed')]) as rpc:
        with pytest.raises(RuntimeError, match='deployment failed'):
            OMV().operation('admin', wire.OPERATION_APPLY_CONFIGURATION, lambda _: None)
        assert rpc.call_args_list[0].args[2] == 'applyChangesBg'
        assert rpc.call_args_list[1].args[2] == 'getOutput'


def test_apply_rejects_missing_task() -> None:
    with patch.object(OMV, 'rpc', return_value=None), pytest.raises(ValueError, match='configuration task'):
        OMV().operation('admin', wire.OPERATION_APPLY_CONFIGURATION, lambda _: None)


def test_native_new_object_identifier_and_folder_names() -> None:
    magic = 'fa4b1c66-ef79-11e5-87a0-0002b3a176b4'
    with patch('protondrive_api.omv.execute', return_value=f'OMV_CONFIGOBJECT_NEW_UUID={magic}\n'):
        assert new_object_id() == magic
    with patch.object(OMV, 'rpc', return_value=['child', 'with spaces']):
        result = OMV().browse('admin', wire.BrowseDirectoriesRequest(relative_path='parent'))
        assert [(item.name, item.relative_path) for item in result.directories] == [
            ('child', 'parent/child'),
            ('with spaces', 'parent/with spaces'),
        ]
