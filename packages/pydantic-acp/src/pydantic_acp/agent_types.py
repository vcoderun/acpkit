from __future__ import annotations as _annotations

from typing import Any, TypeAlias

from pydantic_ai import Agent as PydanticAgent

RuntimeAgent: TypeAlias = PydanticAgent[Any, Any]

__all__ = ("RuntimeAgent",)
