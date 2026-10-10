"""Validate external JSON before exposing it to typed application code."""

import json
from collections.abc import Mapping, Sequence
from typing import TypeAlias, TypeGuard, cast

JSONValue: TypeAlias = None | bool | int | float | str | list['JSONValue'] | dict[str, 'JSONValue']


def is_object(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def validate(value: object) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_object(value):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError('JSON object keys must be strings')
            result[key] = validate(item)
        return result
    if isinstance(value, Sequence):
        return [validate(item) for item in value]
    raise TypeError('Unsupported JSON value')


def decode(text: str | bytes) -> JSONValue:
    # The standard-library decoder has an Any return. Expose only object until
    # recursive validation has established the JSON shape.
    return validate(cast(object, json.loads(text)))


def object_value(value: object) -> dict[str, JSONValue]:
    checked = validate(value)
    if not isinstance(checked, dict):
        raise TypeError('Expected a JSON object')
    return checked


def string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError('Expected a string')
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError('Expected an integer')
    return value


def boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError('Expected a boolean')
    return value
