from __future__ import annotations as _annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Protocol

from pydantic import BaseModel

__all__ = ("DefaultOutputSerializer", "OutputSerializer")


class OutputSerializer(Protocol):
    def serialize(self, value: Any) -> str: ...


def _json_compatible(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, BaseModel):
        return _json_compatible(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible(asdict(value))
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return repr(value)


class DefaultOutputSerializer:
    def serialize(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, BaseModel):
            return json.dumps(_json_compatible(value), indent=2, sort_keys=True)
        if is_dataclass(value) and not isinstance(value, type):
            return json.dumps(_json_compatible(value), indent=2, sort_keys=True)
        if value is None or isinstance(value, dict | list | tuple | int | float | bool):
            return json.dumps(_json_compatible(value), indent=2, sort_keys=True)
        return repr(value)
