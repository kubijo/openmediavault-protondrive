"""External data must be validated before it becomes a typed domain record."""

import unittest

from tests.integration.flow_records import backup_status, flow_record, retry_record

from protondrive import json_data, records
from protondrive.common import BackupError
from protondrive.config import validate


class JSONDataTests(unittest.TestCase):
    def test_nested_values_preserve_content(self) -> None:
        value = json_data.decode('{"items":[null,true,3,1.5,"λ"]}')
        self.assertEqual(value, {'items': [None, True, 3, 1.5, 'λ']})

    def test_non_json_objects_are_rejected(self) -> None:
        for value in (object(), {1: 'invalid key'}, {'nested': object()}):
            with self.subTest(value=value), self.assertRaises(TypeError):
                json_data.validate(value)

    def test_scalar_validation_does_not_coerce(self) -> None:
        with self.assertRaises(TypeError):
            json_data.integer(True)
        with self.assertRaises(TypeError):
            json_data.integer('3')
        with self.assertRaises(TypeError):
            json_data.string(3)
        with self.assertRaises(TypeError):
            json_data.boolean(1)
        with self.assertRaises(TypeError):
            json_data.object_value([])

    def test_invalid_configuration_shape_fails_before_use(self) -> None:
        with self.assertRaises(TypeError):
            validate([])
        with self.assertRaises((KeyError, ValueError, TypeError, BackupError)):
            validate({'enable': False, 'instanceuuid': 123})


class RecordTests(unittest.TestCase):
    def test_remote_listing_rejects_invalid_identity_and_size_types(self) -> None:
        entry = {'name': 'archive', 'uid': 'node', 'type': 'file', 'size': 12}
        self.assertEqual(records.listing([entry]), [entry])
        invalid: tuple[dict[str, object], ...] = (
            {**entry, 'uid': 1},
            {**entry, 'size': True},
            {**entry, 'type': 'unknown'},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                records.listing([value])

    def test_service_status_preserves_activity_and_requires_auth_state(self) -> None:
        value = {'state': 'signed-in', 'url': '', 'error': '', 'transferelapsed': 12, 'transferfile': 'λ.tar.zst'}
        status = records.service_status(value)
        self.assertEqual(status['transferelapsed'], 12)
        self.assertEqual(status['transferfile'], 'λ.tar.zst')
        with self.assertRaises(KeyError):
            records.service_status({})
        with self.assertRaises(TypeError):
            records.service_status({**value, 'transferelapsed': '12'})

    def test_flow_records_reject_malformed_container_and_backup_fields(self) -> None:
        self.assertEqual(flow_record({'containers': ['fixture']})['containers'], ['fixture'])
        with self.assertRaises(TypeError):
            flow_record({'containers': [None]})
        with self.assertRaises(TypeError):
            backup_status({'sets': {'system': {'archive': 123}}})

    def test_retry_record_preserves_owned_payload_and_rejects_corruption(self) -> None:
        source = {'token': 'test-owner', 'payload_identity': [3, 42], 'armed': True, 'retry_since': 12.5}
        value = retry_record(source)
        self.assertEqual(value['token'], 'test-owner')
        self.assertEqual(value['payload_identity'], [3, 42])
        self.assertTrue(value['armed'])
        self.assertEqual(value['retry_since'], 12.5)
        invalid: tuple[object, ...] = (
            {},
            {**source, 'token': None},
            {**source, 'armed': 'false'},
            {**source, 'payload_identity': [True, 42]},
            {**source, 'payload_identity': [3]},
            {**source, 'retry_since': True},
        )
        for record in invalid:
            with self.subTest(record=record), self.assertRaises((KeyError, TypeError, ValueError)):
                retry_record(record)
