"""Keep Tyro's untyped default sentinel out of caller types; validate its result."""

from collections.abc import Callable
from typing import TypeVar

import tyro

OptionsT = TypeVar('OptionsT')


def parse_options(schema: type[OptionsT], parse: Callable[[type[OptionsT]], object] = tyro.cli) -> OptionsT:
    value = parse(schema)
    if not isinstance(value, schema):
        raise TypeError(f'Expected {schema.__name__} CLI options')
    return value
