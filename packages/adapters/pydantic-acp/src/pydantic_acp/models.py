from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import TypeAlias

from acp.schema import ModelInfo
from pydantic_ai.models import Model as PydanticModel

ModelOverride: TypeAlias = str | PydanticModel

__all__ = ("AdapterModel", "ModelOverride")


@dataclass(slots=True, frozen=True, kw_only=True)
class AdapterModel:
    model_id: str
    name: str
    override: ModelOverride
    description: str | None = None

    def to_model_info(self) -> ModelInfo:
        return ModelInfo(
            model_id=self.model_id,
            name=self.name,
            description=self.description,
        )
