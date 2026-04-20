from __future__ import annotations as _annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

SurfaceStatus: TypeAlias = Literal[
    "implemented",
    "partial",
    "intentionally_not_used",
    "planned",
]
SurfaceOwner: TypeAlias = Literal["adapter", "provider", "bridge", "host", "mixed"]

__all__ = (
    "CompatibilityManifest",
    "SurfaceOwner",
    "SurfaceStatus",
    "SurfaceSupport",
)


@dataclass(slots=True, frozen=True, kw_only=True)
class SurfaceSupport:
    status: SurfaceStatus
    owner: SurfaceOwner | None = None
    mapping: str | None = None
    rationale: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class CompatibilityManifest:
    integration_name: str
    adapter: str
    surfaces: Mapping[str, SurfaceSupport]

    def validate(self) -> None:
        if self.integration_name.strip() == "":
            raise ValueError("Compatibility manifest requires a non-empty integration_name.")
        if self.adapter.strip() == "":
            raise ValueError("Compatibility manifest requires a non-empty adapter.")
        if not self.surfaces:
            raise ValueError("Compatibility manifest requires at least one declared surface.")

        for surface_name, support in self.surfaces.items():
            self._validate_surface_name(surface_name)
            self._validate_support(surface_name, support)

    def to_markdown(self) -> str:
        self.validate()
        lines = [
            f"# Compatibility Manifest: {self.integration_name}",
            "",
            f"- Adapter: `{self.adapter}`",
            "",
            "| ACP Surface | Status | Owner | Mapping | Rationale |",
            "| --- | --- | --- | --- | --- |",
        ]
        for surface_name, support in self.surfaces.items():
            lines.append(
                "| "
                f"{surface_name} | "
                f"{_render_cell(support.status)} | "
                f"{_render_cell(support.owner)} | "
                f"{_render_cell(support.mapping)} | "
                f"{_render_cell(support.rationale)} |"
            )
        return "\n".join(lines)

    def _validate_surface_name(self, surface_name: str) -> None:
        normalized = surface_name.strip()
        if normalized == "":
            raise ValueError("Compatibility manifest surface names must not be empty.")
        if normalized != surface_name:
            raise ValueError(
                f"Compatibility manifest surface `{surface_name}` must not have surrounding whitespace."
            )

    def _validate_support(self, surface_name: str, support: SurfaceSupport) -> None:
        if support.status in {"implemented", "partial"} and support.owner is None:
            raise ValueError(
                f"Compatibility manifest surface `{surface_name}` must declare an owner "
                "when status is implemented or partial."
            )
        if support.status == "implemented" and _is_blank(support.mapping):
            raise ValueError(
                f"Compatibility manifest surface `{surface_name}` must declare a mapping "
                "when status is implemented."
            )
        if support.status in {
            "partial",
            "intentionally_not_used",
            "planned",
        } and _is_blank(support.rationale):
            raise ValueError(
                f"Compatibility manifest surface `{surface_name}` must declare a rationale "
                f"when status is {support.status}."
            )
        if support.owner == "mixed" and _is_blank(support.rationale):
            raise ValueError(
                f"Compatibility manifest surface `{surface_name}` must explain how mixed "
                "ownership works."
            )


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _render_cell(value: str | None) -> str:
    if value is None or value.strip() == "":
        return ""
    return value
