from __future__ import annotations as _annotations

import inspect
from collections.abc import Awaitable
from typing import TypeVar

from typing_extensions import TypeIs

ValueT = TypeVar("ValueT")

__all__ = ("is_awaitable", "is_resolved", "resolve_value")


def is_awaitable(value: ValueT | Awaitable[ValueT]) -> TypeIs[Awaitable[ValueT]]:
    return inspect.isawaitable(value)


def is_resolved(value: ValueT | Awaitable[ValueT]) -> TypeIs[ValueT]:
    return not inspect.isawaitable(value)


async def resolve_value(value: ValueT | Awaitable[ValueT]) -> ValueT:
    if is_awaitable(value):
        return await value
    assert is_resolved(value)
    return value
