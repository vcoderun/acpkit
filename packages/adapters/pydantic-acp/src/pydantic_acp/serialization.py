from __future__ import annotations as _annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Protocol

from pydantic import BaseModel

__all__ = ("DefaultOutputSerializer", "OutputSerializer")


class OutputSerializer(Protocol):
    def serialize(self, value: Any) -> str: ...


class DefaultOutputSerializer:
    def serialize(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, BaseModel):
            return value.model_dump_json(indent=2)
        if is_dataclass(value) and not isinstance(value, type):
            return json.dumps(asdict(value), indent=2, sort_keys=True)
        if value is None or isinstance(value, dict | list | tuple | int | float | bool):
            return json.dumps(value, indent=2, sort_keys=True)
        return repr(value)
