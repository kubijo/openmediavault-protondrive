"""Treat decoded tool output as untrusted objects until field validation."""

import json
from collections.abc import Mapping, Sequence
from typing import TypeGuard, cast


def is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def mapping(value: object) -> dict[str, object]:
    if not is_mapping(value):
        raise TypeError('Expected an object')
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError('Expected string keys')
        result[key] = item
    return result


def decode(text: str | bytes) -> dict[str, object]:
    return mapping(decode_value(text))


def decode_value(text: str | bytes) -> object:
    return cast(object, json.loads(text))


def is_list(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, list)


def items(value: object) -> list[object]:
    if not is_list(value):
        raise TypeError('Expected a list')
    return list(value)


def string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError('Expected a string')
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError('Expected an integer')
    return value


def field(value: object, *keys: str) -> object:
    for key in keys:
        value = mapping(value)[key]
    return value
