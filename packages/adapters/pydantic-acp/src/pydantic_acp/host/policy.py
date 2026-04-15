from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias

from ._policy_commands import extract_command_path_candidates, resolve_command_cwd
from ._policy_paths import normalize_host_path, path_is_within_root

HostAccessDisposition: TypeAlias = Literal["allow", "warn", "deny"]
HostRiskCode: TypeAlias = Literal[
    "absolute_path",
    "outside_cwd",
    "outside_workspace",
    "command_cwd_outside_cwd",
    "command_cwd_outside_workspace",
    "command_references_external_paths",
    "command_references_outside_workspace",
]

__all__ = (
    "HostAccessDisposition",
    "HostAccessPolicy",
    "HostCommandEvaluation",
    "HostPathEvaluation",
    "HostRisk",
)

_DISPOSITION_ORDER: Final[dict[HostAccessDisposition, int]] = {
    "allow": 0,
    "warn": 1,
    "deny": 2,
}


@dataclass(slots=True, frozen=True, kw_only=True)
class HostRisk:
    code: HostRiskCode
    message: str
    path: Path | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class HostPathEvaluation:
    disposition: HostAccessDisposition
    resolved_path: Path
    workspace_root: Path | None = None
    risks: tuple[HostRisk, ...] = ()
    is_absolute_input: bool = False
    outside_cwd: bool = False
    outside_workspace: bool = False

    @property
    def has_risks(self) -> bool:
        return bool(self.risks)

    @property
    def should_warn(self) -> bool:
        return self.disposition == "warn"

    @property
    def should_deny(self) -> bool:
        return self.disposition == "deny"

    @property
    def primary_risk(self) -> HostRisk | None:
        return self.risks[0] if self.risks else None

    @property
    def risk_codes(self) -> tuple[HostRiskCode, ...]:
        return tuple(risk.code for risk in self.risks)

    @property
    def headline(self) -> str:
        return _headline_for_disposition(self.disposition)

    @property
    def recommendation(self) -> str:
        return _recommendation_for_disposition(self.disposition)

    @property
    def message(self) -> str:
        if not self.risks:
            return "Host access allowed."
        return "; ".join(risk.message for risk in self.risks)

    def summary_lines(self) -> tuple[str, ...]:
        return _summary_lines(
            headline=self.headline,
            message=self.message,
            recommendation=self.recommendation,
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class HostCommandEvaluation:
    disposition: HostAccessDisposition
    resolved_cwd: Path
    workspace_root: Path | None = None
    referenced_paths: tuple[Path, ...] = ()
    risks: tuple[HostRisk, ...] = ()
    outside_cwd: bool = False
    outside_workspace: bool = False
    references_external_paths: bool = False
    references_outside_workspace: bool = False

    @property
    def has_risks(self) -> bool:
        return bool(self.risks)

    @property
    def should_warn(self) -> bool:
        return self.disposition == "warn"

    @property
    def should_deny(self) -> bool:
        return self.disposition == "deny"

    @property
    def primary_risk(self) -> HostRisk | None:
        return self.risks[0] if self.risks else None

    @property
    def risk_codes(self) -> tuple[HostRiskCode, ...]:
        return tuple(risk.code for risk in self.risks)

    @property
    def headline(self) -> str:
        return _headline_for_disposition(self.disposition)

    @property
    def recommendation(self) -> str:
        return _recommendation_for_disposition(self.disposition)

    @property
    def message(self) -> str:
        if not self.risks:
            return "Host access allowed."
        return "; ".join(risk.message for risk in self.risks)

    def summary_lines(self) -> tuple[str, ...]:
        return _summary_lines(
            headline=self.headline,
            message=self.message,
            recommendation=self.recommendation,
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class HostAccessPolicy:
    absolute_path: HostAccessDisposition = "warn"
    path_outside_cwd: HostAccessDisposition = "warn"
    path_outside_workspace: HostAccessDisposition = "deny"
    command_cwd_outside_cwd: HostAccessDisposition = "warn"
    command_cwd_outside_workspace: HostAccessDisposition = "deny"
    command_external_paths: HostAccessDisposition = "warn"
    command_paths_outside_workspace: HostAccessDisposition = "deny"

    @classmethod
    def strict(cls) -> HostAccessPolicy:
        return cls(
            absolute_path="warn",
            path_outside_cwd="deny",
            path_outside_workspace="deny",
            command_cwd_outside_cwd="deny",
            command_cwd_outside_workspace="deny",
            command_external_paths="deny",
            command_paths_outside_workspace="deny",
        )

    @classmethod
    def permissive(cls) -> HostAccessPolicy:
        return cls(
            absolute_path="warn",
            path_outside_cwd="allow",
            path_outside_workspace="warn",
            command_cwd_outside_cwd="allow",
            command_cwd_outside_workspace="warn",
            command_external_paths="warn",
            command_paths_outside_workspace="warn",
        )

    def evaluate_path(
        self,
        path: str | Path,
        *,
        session_cwd: Path,
        workspace_root: Path | None = None,
    ) -> HostPathEvaluation:
        resolved_path, is_absolute_input = normalize_host_path(path, base_dir=session_cwd)
        normalized_session_cwd = session_cwd.resolve(strict=False)
        normalized_workspace_root = (
            workspace_root.resolve(strict=False) if workspace_root is not None else None
        )

        risks: list[HostRisk] = []
        disposition: HostAccessDisposition = "allow"
        outside_cwd = not path_is_within_root(resolved_path, normalized_session_cwd)
        outside_workspace = normalized_workspace_root is not None and not path_is_within_root(
            resolved_path, normalized_workspace_root
        )

        if is_absolute_input:
            disposition = _stronger_disposition(disposition, self.absolute_path)
            risks.append(
                HostRisk(
                    code="absolute_path",
                    message=f"Path is absolute: {resolved_path}",
                    path=resolved_path,
                )
            )
        if outside_cwd:
            disposition = _stronger_disposition(disposition, self.path_outside_cwd)
            risks.append(
                HostRisk(
                    code="outside_cwd",
                    message=f"Path is outside the active session cwd: {resolved_path}",
                    path=resolved_path,
                )
            )
        if outside_workspace:
            disposition = _stronger_disposition(disposition, self.path_outside_workspace)
            risks.append(
                HostRisk(
                    code="outside_workspace",
                    message=f"Path escapes the workspace root: {resolved_path}",
                    path=resolved_path,
                )
            )

        return HostPathEvaluation(
            disposition=disposition,
            resolved_path=resolved_path,
            workspace_root=normalized_workspace_root,
            risks=tuple(risks),
            is_absolute_input=is_absolute_input,
            outside_cwd=outside_cwd,
            outside_workspace=outside_workspace,
        )

    def enforce_path(
        self,
        path: str | Path,
        *,
        session_cwd: Path,
        workspace_root: Path | None = None,
    ) -> HostPathEvaluation:
        evaluation = self.evaluate_path(
            path,
            session_cwd=session_cwd,
            workspace_root=workspace_root,
        )
        if evaluation.disposition == "deny":
            raise PermissionError(evaluation.message)
        return evaluation

    def evaluate_command(
        self,
        command: str,
        *,
        args: Sequence[str] | None = None,
        cwd: str | Path | None = None,
        session_cwd: Path,
        workspace_root: Path | None = None,
    ) -> HostCommandEvaluation:
        normalized_session_cwd = session_cwd.resolve(strict=False)
        normalized_workspace_root = (
            workspace_root.resolve(strict=False) if workspace_root is not None else None
        )
        resolved_cwd = resolve_command_cwd(cwd, session_cwd=normalized_session_cwd)
        referenced_paths = extract_command_path_candidates(
            command,
            args=args,
            command_cwd=resolved_cwd,
        )

        risks: list[HostRisk] = []
        disposition: HostAccessDisposition = "allow"
        outside_cwd = not path_is_within_root(resolved_cwd, normalized_session_cwd)
        outside_workspace = normalized_workspace_root is not None and not path_is_within_root(
            resolved_cwd, normalized_workspace_root
        )
        referenced_external_paths = tuple(
            path
            for path in referenced_paths
            if not path_is_within_root(path, normalized_session_cwd)
        )
        referenced_outside_workspace = tuple(
            path
            for path in referenced_paths
            if normalized_workspace_root is not None
            and not path_is_within_root(path, normalized_workspace_root)
        )

        if outside_cwd:
            disposition = _stronger_disposition(disposition, self.command_cwd_outside_cwd)
            risks.append(
                HostRisk(
                    code="command_cwd_outside_cwd",
                    message=f"Command cwd is outside the active session cwd: {resolved_cwd}",
                    path=resolved_cwd,
                )
            )
        if outside_workspace:
            disposition = _stronger_disposition(disposition, self.command_cwd_outside_workspace)
            risks.append(
                HostRisk(
                    code="command_cwd_outside_workspace",
                    message=f"Command cwd escapes the workspace root: {resolved_cwd}",
                    path=resolved_cwd,
                )
            )
        if referenced_external_paths:
            disposition = _stronger_disposition(disposition, self.command_external_paths)
            risks.append(
                HostRisk(
                    code="command_references_external_paths",
                    message=(
                        "Command references paths outside the active session cwd: "
                        f"{_join_paths(referenced_external_paths)}"
                    ),
                )
            )
        if referenced_outside_workspace:
            disposition = _stronger_disposition(disposition, self.command_paths_outside_workspace)
            risks.append(
                HostRisk(
                    code="command_references_outside_workspace",
                    message=(
                        "Command references paths outside the workspace root: "
                        f"{_join_paths(referenced_outside_workspace)}"
                    ),
                )
            )

        return HostCommandEvaluation(
            disposition=disposition,
            resolved_cwd=resolved_cwd,
            workspace_root=normalized_workspace_root,
            referenced_paths=referenced_paths,
            risks=tuple(risks),
            outside_cwd=outside_cwd,
            outside_workspace=outside_workspace,
            references_external_paths=bool(referenced_external_paths),
            references_outside_workspace=bool(referenced_outside_workspace),
        )

    def enforce_command(
        self,
        command: str,
        *,
        args: Sequence[str] | None = None,
        cwd: str | Path | None = None,
        session_cwd: Path,
        workspace_root: Path | None = None,
    ) -> HostCommandEvaluation:
        evaluation = self.evaluate_command(
            command,
            args=args,
            cwd=cwd,
            session_cwd=session_cwd,
            workspace_root=workspace_root,
        )
        if evaluation.disposition == "deny":
            raise PermissionError(evaluation.message)
        return evaluation


def _stronger_disposition(
    left: HostAccessDisposition,
    right: HostAccessDisposition,
) -> HostAccessDisposition:
    if _DISPOSITION_ORDER[left] >= _DISPOSITION_ORDER[right]:
        return left
    return right


def _join_paths(paths: Sequence[Path]) -> str:
    return ", ".join(str(path) for path in paths)


def _headline_for_disposition(disposition: HostAccessDisposition) -> str:
    if disposition == "allow":
        return "Host access allowed"
    if disposition == "warn":
        return "Host access requires review"
    return "Host access denied"


def _recommendation_for_disposition(disposition: HostAccessDisposition) -> str:
    if disposition == "allow":
        return "Safe to continue."
    if disposition == "warn":
        return "Review the target carefully before continuing."
    return "Do not continue unless you intentionally relax the policy."


def _summary_lines(
    *,
    headline: str,
    message: str,
    recommendation: str,
) -> tuple[str, ...]:
    return (
        headline,
        message,
        recommendation,
    )
