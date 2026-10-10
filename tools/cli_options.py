"""Keep Tyro's untyped default sentinel out of caller types; validate its result."""

from collections.abc import Callable
from typing import TypeVar, cast

import tyro

OptionsT = TypeVar('OptionsT')


def parse_options(schema: type[OptionsT], parse: Callable[[type[OptionsT]], object] | None = None) -> OptionsT:
    parser = parse if parse is not None else cast(Callable[[type[OptionsT]], object], tyro.cli)
    value = parser(schema)
    if not isinstance(value, schema):
        raise TypeError(f'Expected {schema.__name__} CLI options')
    return value
