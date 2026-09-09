from __future__ import annotations as _annotations

from typing import Any, cast

import pydantic_acp.approvals as approvals_module
import pytest
from pydantic_acp import supports_projection_aware_approval_bridge
from pydantic_acp.approvals import ApprovalResolution
from pydantic_acp.runtime.prompts import dump_message_history, load_message_history
from pydantic_ai import ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import FunctionToolCallEvent, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    Agent,
    ApprovalPolicy,
    ApprovalRequired,
    AvailableCommandsUpdate,
    FileEditToolCallContent,
    FileSystemProjectionMap,
    JsonValue,
    MemorySessionStore,
    NativeApprovalBridge,
    Path,
    PermissionOptionSet,
    PermissionRequestContext,
    RecordingClient,
    RunContext,
    SessionMetadataApprovalPolicyStore,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    agent_message_texts,
    create_acp_agent,
    datetime,
    text_block,
)


class _RecordingPermissionBuilder:
    def __init__(self) -> None:
        self.contexts: list[PermissionRequestContext] = []

    def build_tool_call_update(self, context: PermissionRequestContext) -> ToolCallUpdate:
        self.contexts.append(context)
        return ToolCallUpdate(
            tool_call_id=context.tool_call.tool_call_id,
            title="Custom Permission",
            kind="edit",
            status="pending",
            raw_input=context.raw_input,
        )


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "2"])
def test_adapter_config_rejects_invalid_deferred_approval_round_limit(
    value: Any,
) -> None:
    with pytest.raises(ValueError, match="must be a positive integer"):
        AdapterConfig(max_deferred_approval_rounds=value)


async def test_deferred_approval_allow_flow_resumes_run(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["dangerous"]), deps_type=type(None))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"  # pragma: no cover

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    new_session_response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompt_response = await adapter.prompt(
        prompt=[text_block("Use the dangerous tool.")],
        session_id=new_session_response.session_id,
    )

    assert prompt_response.stop_reason == "end_turn"
    assert client.permission_option_ids
    option_ids = client.permission_option_ids[0][1]
    assert option_ids == ["allow_once", "reject_once"]
    updates = [update for _, update in client.updates]
    tool_updates = [
        update for update in updates if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert isinstance(tool_updates[0], ToolCallStart)
    assert isinstance(tool_updates[1], ToolCallProgress)
    assert tool_updates[1].status == "completed"
    assert agent_message_texts(client) == ['{"dangerous":"approved:a"}']


async def test_deferred_approval_deny_flow_returns_denial_output(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(call_tools=["dangerous"]), deps_type=type(None))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"  # pragma: no cover

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    client.queue_permission_selected("reject_once")
    adapter.on_connect(client)

    new_session_response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompt_response = await adapter.prompt(
        prompt=[text_block("Use the dangerous tool.")],
        session_id=new_session_response.session_id,
    )

    assert prompt_response.stop_reason == "end_turn"
    updates = [update for _, update in client.updates]
    tool_updates = [
        update for update in updates if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert isinstance(tool_updates[1], ToolCallProgress)
    assert tool_updates[1].status == "failed"
    assert agent_message_texts(client) == ['{"dangerous":"The tool call was denied."}']


async def test_deferred_approval_supports_more_than_eight_sequential_rounds(
    tmp_path: Path,
) -> None:
    approval_count = 10

    def route_tools(
        messages: list[ModelRequest | ModelResponse],
        info: AgentInfo,
    ) -> ModelResponse:
        del info
        completed = sum(
            1
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if getattr(part, "tool_name", None) == "dangerous"
        )
        if completed == approval_count:
            return ModelResponse(parts=[TextPart("all approvals completed")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "dangerous",
                    {"value": completed},
                    tool_call_id=f"dangerous-{completed}",
                )
            ]
        )

    agent = Agent(
        FunctionModel(route_tools, model_name="sequential-approval-model"),
        deps_type=type(None),
    )

    @agent.tool
    def dangerous(ctx: RunContext[None], value: int) -> int:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return value

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    for _ in range(approval_count):
        client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    response = await adapter.prompt(
        prompt=[text_block("Run every approved tool.")],
        session_id=session.session_id,
    )

    assert response.stop_reason == "end_turn"
    assert len(client.permission_option_ids) == approval_count
    assert agent_message_texts(client) == ["all approvals completed"]


async def test_deferred_approval_cancel_flow_stops_turn(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["dangerous"]), deps_type=type(None))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"  # pragma: no cover

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    client.queue_permission_cancelled()
    adapter.on_connect(client)

    new_session_response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompt_response = await adapter.prompt(
        prompt=[text_block("Use the dangerous tool.")],
        session_id=new_session_response.session_id,
    )

    assert prompt_response.stop_reason == "cancelled"
    tool_updates = [
        update
        for _, update in client.updates
        if not isinstance(update, AvailableCommandsUpdate)
        and isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) == 2
    assert isinstance(tool_updates[0], ToolCallStart)
    assert isinstance(tool_updates[1], ToolCallProgress)
    assert tool_updates[1].status == "failed"
    assert tool_updates[1].raw_output == "Permission request cancelled."

    stored_session = cast("Any", adapter)._config.session_store.get(new_session_response.session_id)
    assert stored_session is not None
    message_history = load_message_history(stored_session.message_history_json)
    assert not any(
        isinstance(part, ToolCallPart)
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert any(
        isinstance(part, TextPart) and "Permission request cancelled." in part.content
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_prompt_error_sanitizes_unprocessed_tool_calls_and_records_traceback(
    tmp_path: Path,
) -> None:
    def route_failing_tool(
        messages: list[ModelRequest | ModelResponse],
        info: AgentInfo,
    ) -> ModelResponse:
        del info
        if messages and isinstance(messages[-1], ModelRequest):  # pragma: no branch
            for part in messages[-1].parts:
                if isinstance(part, UserPromptPart):  # pragma: no branch
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "dangerous",
                                {"path": "boom.txt"},
                                tool_call_id="dangerous-call",
                            ),
                        ],
                    )
        raise AssertionError("expected the failing tool call to be requested")  # pragma: no cover

    agent = Agent(
        FunctionModel(route_failing_tool, model_name="failing-tool-model"),
        deps_type=type(None),
    )

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        del ctx, path
        raise RuntimeError("tool exploded")

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session_response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    stored_session = cast("Any", adapter)._config.session_store.get(session_response.session_id)
    assert stored_session is not None
    stored_session.message_history_json = dump_message_history(
        [
            ModelRequest(parts=[UserPromptPart("previous prompt")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "dangling_tool",
                        {"path": "a"},
                        tool_call_id="call-1",
                    ),
                ],
                model_name="test",
            ),
        ],
    )
    cast("Any", adapter)._config.session_store.save(stored_session)

    with pytest.raises(RuntimeError, match="tool exploded"):
        await adapter.prompt(
            prompt=[text_block("Trigger the failing tool.")],
            session_id=session_response.session_id,
        )

    updated_session = cast("Any", adapter)._config.session_store.get(session_response.session_id)
    assert updated_session is not None
    message_history = load_message_history(updated_session.message_history_json)
    assert not any(
        isinstance(part, ToolCallPart)
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert any(
        isinstance(part, UserPromptPart) and part.content == "Trigger the failing tool."
        for message in message_history
        if not isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert any(
        isinstance(part, TextPart) and "RuntimeError: tool exploded" in part.content
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


async def test_prompt_error_closes_tool_call_started_by_stream(tmp_path: Path) -> None:
    agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)
    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    stored_session = cast("Any", adapter)._config.session_store.get(session.session_id)
    assert stored_session is not None
    await cast("Any", adapter)._record_update(
        stored_session,
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="preexisting-background-call",
            title="Existing background call",
            kind="other",
            status="in_progress",
        ),
    )
    cast("Any", adapter)._config.session_store.save(stored_session)

    async def failing_stream(prompt_text: str | None, **kwargs: Any):
        del prompt_text, kwargs
        yield FunctionToolCallEvent(
            ToolCallPart(
                "dangerous",
                {"path": "boom.txt"},
                tool_call_id="streamed-dangerous-call",
            )
        )
        raise RuntimeError("streamed tool exploded")

    cast("Any", agent).run_stream_events = failing_stream
    cast("Any", adapter)._should_stream_text_responses = lambda *args, **kwargs: True

    with pytest.raises(RuntimeError, match="streamed tool exploded"):
        await adapter.prompt(
            prompt=[text_block("Trigger the streamed failure.")],
            session_id=session.session_id,
        )

    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
        and update.tool_call_id == "streamed-dangerous-call"
    ]
    assert isinstance(tool_updates[0], ToolCallStart)
    assert isinstance(tool_updates[-1], ToolCallProgress)
    assert tool_updates[-1].status == "failed"
    assert tool_updates[-1].raw_output == "Tool call interrupted by RuntimeError."
    preexisting_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
        and update.tool_call_id == "preexisting-background-call"
    ]
    assert len(preexisting_updates) == 1
    assert isinstance(preexisting_updates[0], ToolCallStart)


async def test_deferred_approval_write_projection_keeps_diff_after_approval(
    tmp_path: Path,
) -> None:
    (tmp_path / "a").write_text("before", encoding="utf-8")
    agent = Agent(TestModel(call_tools=["write_file"]), deps_type=type(None))

    @agent.tool
    def write_file(ctx: RunContext[None], path: str, content: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_write_tool="write_file")],
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    new_session_response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompt_response = await adapter.prompt(
        prompt=[text_block("Use the write tool.")],
        session_id=new_session_response.session_id,
    )

    assert prompt_response.stop_reason == "end_turn"
    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) >= 2

    tool_start = tool_updates[0]
    tool_progress = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert isinstance(tool_progress, ToolCallProgress)
    assert tool_start.content is not None
    assert tool_progress.content is not None

    start_diff = tool_start.content[0]
    progress_diff = tool_progress.content[0]
    assert isinstance(start_diff, FileEditToolCallContent)
    assert isinstance(progress_diff, FileEditToolCallContent)
    assert start_diff.path == "a"
    assert start_diff.old_text == "before"
    assert start_diff.new_text == "a"
    assert progress_diff.path == "a"
    assert progress_diff.old_text == "before"
    assert progress_diff.new_text == "a"
    assert tool_progress.status == "completed"


async def test_deferred_approval_permission_request_uses_projection_content(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(call_tools=["write_file"]), deps_type=type(None))

    @agent.tool
    def write_file(ctx: RunContext[None], path: str, content: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"  # pragma: no cover

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_write_tool="write_file")],
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    await adapter.prompt(
        prompt=[text_block("Use the write tool.")],
        session_id=session.session_id,
    )

    permission_request = client.permission_option_ids[0][2]
    assert permission_request.status == "pending"
    assert permission_request.content is not None
    content = permission_request.content[0]
    assert isinstance(content, FileEditToolCallContent)
    assert content.path == "a"
    assert content.new_text == "a"


async def test_native_approval_bridge_uses_custom_builder_store_and_labels(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(call_tools=["write_file"]), deps_type=type(None))

    @agent.tool
    def write_file(ctx: RunContext[None], path: str, content: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"  # pragma: no cover

    class Store:
        def __init__(self) -> None:
            self.policies: dict[str, ApprovalPolicy] = {}
            self.get_calls: list[str] = []
            self.set_calls: list[tuple[str, ApprovalPolicy]] = []

        def get_policy(
            self,
            session: AcpSessionContext,
            policy_key: str,
        ) -> ApprovalPolicy | None:
            del session
            self.get_calls.append(policy_key)
            return self.policies.get(policy_key)

        def set_policy(
            self,
            session: AcpSessionContext,
            policy_key: str,
            policy: ApprovalPolicy,
        ) -> None:
            del session
            self.set_calls.append((policy_key, policy))
            self.policies[policy_key] = policy

        def export_state(self, session: AcpSessionContext) -> dict[str, JsonValue]:
            del session
            return dict(self.policies)

    store = Store()
    builder = _RecordingPermissionBuilder()
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(
            approval_bridge=NativeApprovalBridge(
                enable_persistent_choices=True,
                option_set=PermissionOptionSet(
                    allow_once_name="Allow this",
                    reject_once_name="Deny this",
                    allow_always_name="Always yes",
                    reject_always_name="Always no",
                ),
                policy_store=store,
                tool_call_builder=builder,
            ),
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_always")
    adapter.on_connect(client)

    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    await adapter.prompt(
        prompt=[text_block("Use the write tool.")],
        session_id=session.session_id,
    )

    assert client.permission_option_ids[0][1] == [
        "allow_once",
        "allow_always",
        "reject_once",
        "reject_always",
    ]
    assert client.permission_option_names[0][1] == [
        "Allow this",
        "Always yes",
        "Deny this",
        "Always no",
    ]
    assert client.permission_option_ids[0][2].title == "Custom Permission"
    assert len(builder.contexts) == 1
    assert store.get_calls == ["write_file"]
    assert store.set_calls == [("write_file", "allow")]
    export_session = AcpSessionContext(
        session_id=session.session_id,
        cwd=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert store.export_state(export_session) == {"write_file": "allow"}


async def test_native_approval_bridge_live_policy_lookup_does_not_export_state(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(call_tools=["dangerous"]), deps_type=type(None))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"  # pragma: no cover

    class Store:
        def __init__(self) -> None:
            self.get_calls: list[str] = []

        def get_policy(
            self,
            session: AcpSessionContext,
            policy_key: str,
        ) -> ApprovalPolicy | None:
            del session
            self.get_calls.append(policy_key)
            return "allow"

        def set_policy(
            self,
            session: AcpSessionContext,
            policy_key: str,
            policy: ApprovalPolicy,
        ) -> None:  # pragma: no cover
            del session, policy_key, policy
            raise AssertionError("remembered policy should not be rewritten")

        def export_state(
            self,
            session: AcpSessionContext,
        ) -> dict[str, JsonValue]:  # pragma: no cover
            del session
            raise AssertionError("export_state is metadata-only, not live approval lookup")

    store = Store()
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(
            approval_bridge=NativeApprovalBridge(
                enable_persistent_choices=True,
                policy_store=store,
            ),
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    response = await adapter.prompt(
        prompt=[text_block("Use the dangerous tool.")],
        session_id=session.session_id,
    )

    assert response.stop_reason == "end_turn"
    assert store.get_calls == ["dangerous"]
    assert client.permission_option_ids == []
    assert agent_message_texts(client) == ['{"dangerous":"approved:a"}']


def test_session_metadata_approval_policy_store_reads_valid_policy(
    tmp_path: Path,
) -> None:
    session = AcpSessionContext(
        session_id="approval-store",
        cwd=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={"approval_policies": {"write_file": "allow"}},
    )
    store = SessionMetadataApprovalPolicyStore()

    assert store.get_policy(session, "write_file") == "allow"


async def test_projection_aware_approval_bridge_detection_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingCallable:
        resolve_deferred_approvals = None

    class VarKeywordBridge:
        async def resolve_deferred_approvals(self, **kwargs: Any) -> ApprovalResolution:
            raise AssertionError("not called")

    class SignatureBridge:
        async def resolve_deferred_approvals(self) -> ApprovalResolution:
            raise AssertionError("not called")

    assert not supports_projection_aware_approval_bridge(MissingCallable())
    assert supports_projection_aware_approval_bridge(VarKeywordBridge())
    with pytest.raises(AssertionError, match="not called"):
        await VarKeywordBridge().resolve_deferred_approvals()

    def raise_signature_error(value: object) -> object:
        del value
        raise ValueError("signature unavailable")

    monkeypatch.setattr(approvals_module, "signature", raise_signature_error)
    assert not supports_projection_aware_approval_bridge(SignatureBridge())
    with pytest.raises(AssertionError, match="not called"):
        await SignatureBridge().resolve_deferred_approvals()


async def test_legacy_approval_bridge_without_projection_signature_still_runs(
    tmp_path: Path,
) -> None:
    class LegacyBridge:
        def __init__(self) -> None:
            self.called = False

        async def resolve_deferred_approvals(
            self,
            *,
            client: Any,
            session: AcpSessionContext,
            requests: DeferredToolRequests,
            classifier: Any,
        ) -> ApprovalResolution:
            del client, session, classifier
            self.called = True
            results = DeferredToolResults(metadata=dict(requests.metadata))
            for tool_call in requests.approvals:
                results.approvals[tool_call.tool_call_id] = ToolApproved()
            return ApprovalResolution(deferred_tool_results=results)

    bridge = LegacyBridge()
    agent = Agent(TestModel(call_tools=["dangerous"]), deps_type=type(None))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(
            approval_bridge=bridge,
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    response = await adapter.prompt(
        prompt=[text_block("Use the dangerous tool.")],
        session_id=session.session_id,
    )

    assert response.stop_reason == "end_turn"
    assert bridge.called
    assert client.permission_option_ids == []
    assert agent_message_texts(client) == ['{"dangerous":"approved:a"}']


async def test_deferred_approval_write_projection_preserves_pre_write_diff_after_file_changes(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "a"
    target_path.write_text("before", encoding="utf-8")
    agent = Agent(TestModel(call_tools=["write_file"]), deps_type=type(None))

    @agent.tool
    def write_file(ctx: RunContext[None], path: str, content: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        resolved_path = tmp_path / path
        resolved_path.write_text(content, encoding="utf-8")
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_write_tool="write_file")],
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    new_session_response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompt_response = await adapter.prompt(
        prompt=[text_block("Use the write tool.")],
        session_id=new_session_response.session_id,
    )

    assert prompt_response.stop_reason == "end_turn"
    assert target_path.read_text(encoding="utf-8") == "a"
    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) >= 2

    tool_start = tool_updates[0]
    tool_progress = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert isinstance(tool_progress, ToolCallProgress)
    assert tool_start.content is not None
    assert tool_progress.content is not None

    start_diff = tool_start.content[0]
    progress_diff = tool_progress.content[0]
    assert isinstance(start_diff, FileEditToolCallContent)
    assert isinstance(progress_diff, FileEditToolCallContent)
    assert start_diff.old_text == "before"
    assert start_diff.new_text == "a"
    assert progress_diff.old_text == "before"
    assert progress_diff.new_text == "a"
    assert tool_progress.status == "completed"


async def test_prompt_without_generic_tool_projection_omits_tool_updates(
    tmp_path: Path,
) -> None:
    tool_model = TestModel(call_tools=["read_file"], custom_output_text="projection-disabled")
    agent = Agent(tool_model)

    @agent.tool_plain
    def read_file(path: str) -> str:
        return f"contents:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(
            enable_generic_tool_projection=False,
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    await adapter.prompt(
        prompt=[text_block("Do not emit tool updates.")],
        session_id=session.session_id,
    )

    assert not any(
        isinstance(update, ToolCallStart | ToolCallProgress) for _, update in client.updates
    )
    assert agent_message_texts(client) == ["projection-disabled"]
