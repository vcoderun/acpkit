from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias

from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import PermissionOption, ToolCallUpdate
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from typing_extensions import TypeIs

from .projection import ToolClassifier, extract_tool_call_locations
from .session.state import AcpSessionContext, JsonValue

ApprovalPolicy: TypeAlias = Literal["allow", "reject"]

__all__ = ("ApprovalBridge", "ApprovalResolution", "NativeApprovalBridge")

_APPROVAL_POLICIES_KEY: Final = "approval_policies"


def _is_approval_policy(value: JsonValue) -> TypeIs[ApprovalPolicy]:
    return value in {"allow", "reject"}


@dataclass(slots=True, frozen=True, kw_only=True)
class ApprovalResolution:
    deferred_tool_results: DeferredToolResults
    cancelled: bool = False
    cancelled_tool_call: ToolCallPart | None = None


class ApprovalBridge(Protocol):
    async def resolve_deferred_approvals(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        requests: DeferredToolRequests,
        classifier: ToolClassifier,
    ) -> ApprovalResolution: ...


@dataclass(slots=True, kw_only=True)
class NativeApprovalBridge:
    enable_persistent_choices: bool = False

    async def resolve_deferred_approvals(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        requests: DeferredToolRequests,
        classifier: ToolClassifier,
    ) -> ApprovalResolution:
        deferred_results = DeferredToolResults(metadata=dict(requests.metadata))
        for tool_call in requests.approvals:
            raw_input = tool_call.args_as_dict()
            approval_policy_key = classifier.approval_policy_key(tool_call.tool_name, raw_input)
            remembered_policy = self._get_remembered_policy(session, approval_policy_key)
            if remembered_policy is not None:
                deferred_results.approvals[tool_call.tool_call_id] = self._policy_to_result(
                    remembered_policy
                )
                continue

            permission_response = await client.request_permission(
                options=self._build_permission_options(),
                session_id=session.session_id,
                tool_call=self._build_tool_call_update(tool_call, classifier),
            )
            outcome = permission_response.outcome
            if outcome.outcome == "cancelled":
                return ApprovalResolution(
                    deferred_tool_results=deferred_results,
                    cancelled=True,
                    cancelled_tool_call=tool_call,
                )

            selected_result = self._selected_option_to_result(outcome.option_id)
            if selected_result is None:
                raise RequestError.invalid_request({"optionId": outcome.option_id})
            self._remember_policy(
                session=session,
                approval_policy_key=approval_policy_key,
                option_id=outcome.option_id,
            )
            deferred_results.approvals[tool_call.tool_call_id] = selected_result

        return ApprovalResolution(deferred_tool_results=deferred_results)

    def _build_permission_options(self) -> list[PermissionOption]:
        options = [
            PermissionOption(option_id="allow_once", name="Allow", kind="allow_once"),
            PermissionOption(option_id="reject_once", name="Deny", kind="reject_once"),
        ]
        if not self.enable_persistent_choices:
            return options
        return [
            PermissionOption(option_id="allow_once", name="Allow Once", kind="allow_once"),
            PermissionOption(option_id="allow_always", name="Always Allow", kind="allow_always"),
            PermissionOption(option_id="reject_once", name="Deny Once", kind="reject_once"),
            PermissionOption(option_id="reject_always", name="Always Deny", kind="reject_always"),
        ]

    def _build_tool_call_update(
        self, tool_call: ToolCallPart, classifier: ToolClassifier
    ) -> ToolCallUpdate:
        raw_input = tool_call.args_as_dict()
        return ToolCallUpdate(
            tool_call_id=tool_call.tool_call_id,
            title=tool_call.tool_name,
            kind=classifier.classify(tool_call.tool_name, raw_input),
            locations=extract_tool_call_locations(raw_input),
            raw_input=raw_input,
            status="in_progress",
        )

    def _selected_option_to_result(
        self,
        option_id: str,
    ) -> ToolApproved | ToolDenied | None:
        if option_id in {"allow_once", "allow_always"}:
            return ToolApproved()
        if option_id in {"reject_once", "reject_always"}:
            return ToolDenied()
        return None

    def _remember_policy(
        self,
        *,
        session: AcpSessionContext,
        approval_policy_key: str,
        option_id: str,
    ) -> None:
        if not self.enable_persistent_choices:
            return
        if option_id == "allow_always":
            self._set_remembered_policy(
                session,
                approval_policy_key=approval_policy_key,
                policy="allow",
            )
        elif option_id == "reject_always":
            self._set_remembered_policy(
                session,
                approval_policy_key=approval_policy_key,
                policy="reject",
            )

    def _get_remembered_policy(
        self,
        session: AcpSessionContext,
        approval_policy_key: str,
    ) -> ApprovalPolicy | None:
        policies = self._approval_policies(session)
        remembered = policies.get(approval_policy_key)
        if _is_approval_policy(remembered):
            return remembered
        return None

    def _set_remembered_policy(
        self,
        session: AcpSessionContext,
        *,
        approval_policy_key: str,
        policy: ApprovalPolicy,
    ) -> None:
        raw_policies = session.metadata.get(_APPROVAL_POLICIES_KEY)
        if not isinstance(raw_policies, dict):
            raw_policies = {}
            session.metadata[_APPROVAL_POLICIES_KEY] = raw_policies
        raw_policies[approval_policy_key] = policy

    def _approval_policies(self, session: AcpSessionContext) -> dict[str, JsonValue]:
        raw_policies = session.metadata.get(_APPROVAL_POLICIES_KEY)
        if isinstance(raw_policies, dict):
            return raw_policies
        return {}

    def _policy_to_result(self, policy: ApprovalPolicy) -> ToolApproved | ToolDenied:
        if policy == "allow":
            return ToolApproved()
        return ToolDenied()
