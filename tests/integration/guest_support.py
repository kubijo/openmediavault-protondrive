"""Typed boundaries for standalone disposable guest checks."""

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Literal, TypedDict, TypeGuard, Unpack, cast, overload


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


class RunOptions(TypedDict, total=False):
    cwd: str | Path
    stdin: int | IO[bytes] | IO[str] | None
    stdout: int | IO[bytes] | IO[str] | None
    stderr: int | IO[bytes] | IO[str] | None
    capture_output: bool
    timeout: float | None
    env: Mapping[str, str] | None
    start_new_session: bool


@overload
def run(
    *args: str, text: Literal[True], input: str | None = None, check: bool = True, **kwargs: Unpack[RunOptions]
) -> subprocess.CompletedProcess[str]: ...


@overload
def run(
    *args: str,
    text: Literal[False] = False,
    input: bytes | None = None,
    check: bool = True,
    **kwargs: Unpack[RunOptions],
) -> subprocess.CompletedProcess[bytes]: ...


def run(
    *args: str, text: bool = False, input: str | bytes | None = None, check: bool = True, **kwargs: Unpack[RunOptions]
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    print('RUN', args, flush=True)
    if text:
        if input is not None and not isinstance(input, str):
            raise TypeError('Text commands require string input')
        return subprocess.run(args, text=True, input=input, check=check, **kwargs)
    if input is not None and not isinstance(input, bytes):
        raise TypeError('Binary commands require bytes input')
    return subprocess.run(args, input=input, check=check, **kwargs)
