from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Any, Protocol

from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import PermissionOption, ToolCallUpdate

from .projection import ToolClassifier, extract_tool_call_locations
from .session.state import AcpSessionContext

__all__ = ("ApprovalBridge", "ApprovalDecision", "NativeApprovalBridge")


@dataclass(slots=True, frozen=True, kw_only=True)
class ApprovalDecision:
    decisions: list[dict[str, Any]]
    cancelled: bool = False


class ApprovalBridge(Protocol):
    async def resolve_action_requests(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        action_requests: list[dict[str, Any]],
        review_configs: list[dict[str, Any]],
        classifier: ToolClassifier,
    ) -> ApprovalDecision: ...


@dataclass(slots=True, kw_only=True)
class NativeApprovalBridge:
    async def resolve_action_requests(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        action_requests: list[dict[str, Any]],
        review_configs: list[dict[str, Any]],
        classifier: ToolClassifier,
    ) -> ApprovalDecision:
        decisions: list[dict[str, Any]] = []
        config_by_action = {
            config.get("action_name"): config
            for config in review_configs
            if isinstance(config, dict)
        }
        for action_request in action_requests:
            if not isinstance(action_request, dict):
                raise RequestError.invalid_request({"action_request": action_request})
            tool_name = action_request.get("name")
            tool_args = action_request.get("args", {})
            if not isinstance(tool_name, str) or not isinstance(tool_args, dict):
                raise RequestError.invalid_request({"action_request": action_request})
            review_config = config_by_action.get(tool_name, {})
            allowed_decisions = review_config.get("allowed_decisions", ["approve", "reject"])
            if "edit" in allowed_decisions and set(allowed_decisions) == {"edit"}:
                raise RequestError.invalid_request(
                    {"reason": "ACP permission prompts cannot collect edited tool arguments."}
                )
            permission = await client.request_permission(
                session_id=session.session_id,
                options=self._build_permission_options(),
                tool_call=self._build_tool_call_update(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    classifier=classifier,
                ),
            )
            outcome = permission.outcome
            if outcome.outcome == "cancelled":
                return ApprovalDecision(decisions=decisions, cancelled=True)
            option_id = getattr(outcome, "option_id", None)
            if option_id == "allow_once":
                decisions.append({"type": "approve"})
                continue
            if option_id == "reject_once":
                decisions.append({"type": "reject"})
                continue
            raise RequestError.invalid_request({"optionId": option_id})
        return ApprovalDecision(decisions=decisions)

    def _build_permission_options(self) -> list[PermissionOption]:
        return [
            PermissionOption(option_id="allow_once", name="Allow", kind="allow_once"),
            PermissionOption(option_id="reject_once", name="Deny", kind="reject_once"),
        ]

    def _build_tool_call_update(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        classifier: ToolClassifier,
    ) -> ToolCallUpdate:
        return ToolCallUpdate(
            tool_call_id=f"hitl:{tool_name}",
            title=tool_name,
            kind=classifier.classify(tool_name, tool_args),
            locations=extract_tool_call_locations(tool_args),
            raw_input=tool_args,
            status="in_progress",
        )
