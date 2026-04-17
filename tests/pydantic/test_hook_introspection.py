from __future__ import annotations as _annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_acp.hook_projection import HookEvent
from pydantic_acp.runtime.hook_introspection import (
    _call_hook_func,
    _HookUpdateEmitter,
    _override_root_capability,
    _root_capability,
    _summarize_error,
    _summarize_result,
    _tool_filters,
    _tool_name,
    _wrap_hook_entry,
    _wrap_hooks,
    list_agent_hooks,
    observe_agent_hooks,
)
from pydantic_ai.capabilities import CapabilityOrdering, CombinedCapability, Hooks, HookTimeoutError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from typing_extensions import Sentinel

from .support import (
    AdapterConfig,
    Agent,
    HookProjectionMap,
    MemorySessionStore,
    RecordingClient,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    create_acp_agent,
    text_block,
)

_INVALID_HOOK_VALUE = Sentinel("_INVALID_HOOK_VALUE")


@dataclass
class _TestHookEntry:
    func: Any
    timeout: float | None = None
    tools: frozenset[str] | None = None


class _ReadOnlyHookEntry:
    __slots__ = ("_func", "_timeout", "tools")

    def __init__(
        self,
        func: Any,
        timeout: float | None = None,
        tools: frozenset[str] | None = None,
    ) -> None:
        self._func = func
        self._timeout = timeout
        self.tools = tools

    def __copy__(self) -> _ReadOnlyHookEntry:
        return self

    @property
    def func(self) -> Any:
        return self._func

    @property
    def timeout(self) -> float | None:
        return self._timeout


def test_existing_before_model_request_hook_emits_acp_updates(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_model_request
    async def annotate_request(ctx, request_context):
        del ctx
        return request_context

    adapter = create_acp_agent(
        agent=Agent(
            TestModel(custom_output_text="hooked"),
            name="hooked-agent",
            capabilities=[hooks],
        ),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Trigger the hook.")],
            session_id=session.session_id,
        )
    )

    start_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart)
        and update.title == "Hook Before Model (annotate_request)"
    ]
    progress_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress)
        and update.title == "Hook Before Model (annotate_request)"
    ]

    assert len(start_updates) == 1
    assert start_updates[0].raw_input == {
        "event": "before_model_request",
        "hook": "annotate_request",
    }
    assert start_updates[0].kind == "fetch"
    assert len(progress_updates) == 1
    assert progress_updates[0].status == "completed"
    assert isinstance(progress_updates[0].raw_output, str)
    assert progress_updates[0].raw_output.startswith("messages=")


def test_wrap_run_event_stream_hook_requires_async_iterable_and_emits_failed_update() -> None:
    visible_updates: list[Any] = []

    async def visible_write(update: Any) -> None:
        visible_updates.append(update)

    visible_emitter = _HookUpdateEmitter(
        write_update=visible_write,
        projection_map=HookProjectionMap(),
        run_id="visible-stream",
    )

    def invalid_stream_hook(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "not-a-stream"

    wrapped_entry, changed = _wrap_hook_entry(
        "wrap_run_event_stream",
        _TestHookEntry(invalid_stream_hook),
        emitter=visible_emitter,
    )

    assert changed is True

    async def empty_stream() -> Any:
        if False:
            yield None

    with pytest.raises(TypeError, match="async iterable"):

        async def consume() -> None:
            async for _ in cast(Any, wrapped_entry).func(cast(Any, None), stream=empty_stream()):
                pass

        asyncio.run(consume())

    assert any(getattr(update, "status", None) == "failed" for update in visible_updates)


def test_multiple_registered_hook_callbacks_each_emit_updates(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_model_request
    async def first_request_hook(ctx, request_context):
        del ctx
        return request_context

    @hooks.on.before_model_request
    async def second_request_hook(ctx, request_context):
        del ctx
        return request_context

    adapter = create_acp_agent(
        agent=Agent(
            TestModel(custom_output_text="hooked"),
            capabilities=[hooks],
        ),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Trigger the registered hooks.")],
            session_id=session.session_id,
        )
    )

    start_titles = [
        update.title for _, update in client.updates if isinstance(update, ToolCallStart)
    ]
    progress_titles = [
        update.title for _, update in client.updates if isinstance(update, ToolCallProgress)
    ]

    assert start_titles.count("Hook Before Model (first_request_hook)") == 1
    assert start_titles.count("Hook Before Model (second_request_hook)") == 1
    assert progress_titles.count("Hook Before Model (first_request_hook)") == 1
    assert progress_titles.count("Hook Before Model (second_request_hook)") == 1


def test_tool_filtered_hook_preserves_tool_metadata(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_tool_execute(tools=["echo"])
    async def audit_echo(ctx, *, call, tool_def, args):
        del ctx, call, tool_def
        return args

    agent = Agent(
        TestModel(call_tools=["echo"], custom_output_text="tool-hooked"),
        capabilities=[hooks],
    )

    @agent.tool_plain
    def echo(text: str) -> str:
        return text.upper()

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Call the echo tool.")],
            session_id=session.session_id,
        )
    )

    start_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart)
        and update.title == "Hook Before Tool [echo] (audit_echo)"
    ]
    progress_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress)
        and update.title == "Hook Before Tool [echo] (audit_echo)"
    ]

    assert len(start_updates) == 1
    assert start_updates[0].raw_input == {
        "event": "before_tool_execute",
        "hook": "audit_echo",
        "tools": ["echo"],
        "tool_name": "echo",
    }
    assert start_updates[0].kind == "execute"
    assert len(progress_updates) == 1
    assert progress_updates[0].status == "completed"
    assert progress_updates[0].raw_output == "dict"


def test_custom_hook_projection_map_can_hide_and_relabel_events(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_model_request
    async def annotate_request(ctx, request_context):
        del ctx
        return request_context

    adapter = create_acp_agent(
        agent=Agent(
            TestModel(custom_output_text="hooked"),
            capabilities=[hooks],
        ),
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[
            HookProjectionMap(
                event_labels={"before_model_request": "Model Hook"},
                show_hook_name_in_title=False,
                include_tool_filters=False,
            )
        ],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Trigger the hook.")],
            session_id=session.session_id,
        )
    )

    start_update = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    progress_update = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert start_update.title == "Hook Model Hook"
    assert progress_update.title == "Hook Model Hook"
    assert start_update.raw_input == {
        "event": "before_model_request",
        "hook": "annotate_request",
    }


def test_hook_introspection_private_helpers_cover_noops_invalid_entries_and_timeouts() -> None:
    recorded_updates: list[Any] = []

    async def write_update(update: Any) -> None:
        recorded_updates.append(update)

    hidden_emitter = _HookUpdateEmitter(
        write_update=write_update,
        projection_map=HookProjectionMap(hidden_event_ids=frozenset({"hidden"})),
        run_id="run",
    )
    hidden_event = HookEvent(
        event_id="hidden",
        hook_name="hidden_hook",
        tool_name=None,
        tool_filters=(),
    )
    assert asyncio.run(hidden_emitter.emit_start(event=hidden_event)) is None
    assert asyncio.run(hidden_emitter.emit_progress(tool_call_id=None, event=hidden_event)) is None
    assert asyncio.run(hidden_emitter.emit(event=hidden_event)) is None
    assert recorded_updates == []

    plain_agent = Agent(TestModel(custom_output_text="plain"))
    with observe_agent_hooks(
        plain_agent,
        write_update=write_update,
        projection_map=HookProjectionMap(),
    ):
        pass

    invalid_agent = Agent(TestModel(custom_output_text="invalid"))
    cast(Any, invalid_agent)._root_capability = _INVALID_HOOK_VALUE
    cast(Any, invalid_agent)._override_root_capability = _INVALID_HOOK_VALUE
    with observe_agent_hooks(
        invalid_agent,
        write_update=write_update,
        projection_map=HookProjectionMap(),
    ):
        pass
    assert _root_capability(invalid_agent) is None
    assert _override_root_capability(invalid_agent) is None

    hooks = Hooks[None]()

    async def alpha(ctx, request_context):
        del ctx
        return request_context

    async def skipped_alpha(ctx, request_context):
        del ctx
        return request_context

    async def beta(ctx, *, call, tool_def, args):
        del ctx, call, tool_def
        return args

    skipped = _TestHookEntry(skipped_alpha)
    skipped.func.__module__ = "pydantic_acp.bridges.hooks"
    tool_entry = _TestHookEntry(beta, tools=frozenset({"z", "a"}))
    broken_entry = _TestHookEntry(alpha)
    cast(Any, broken_entry).func = None
    cast(Any, hooks)._registry = {
        1: [],
        "before_model_request": ["bad", broken_entry, skipped, _TestHookEntry(alpha)],
        "before_tool_execute": [tool_entry],
    }

    listed = list_agent_hooks(Agent(TestModel(custom_output_text="hooked"), capabilities=[hooks]))
    assert [(info.event_id, info.hook_name, info.tool_filters) for info in listed] == [
        ("before_model_request", "alpha", ()),
        ("before_tool_execute", "beta", ("a", "z")),
    ]
    assert _tool_filters(cast(Any, SimpleNamespace(tools=None))) == ()
    assert _tool_name({"call": _INVALID_HOOK_VALUE}) is None

    class SilentError(Exception):
        def __str__(self) -> str:
            return ""

    assert _summarize_error(SilentError()) == "SilentError"
    assert _summarize_result(None) == "completed"
    assert _summarize_result(3) == "3"
    assert _summarize_result(ToolCallPart("echo", {"text": "ok"})) == "echo"
    assert _summarize_result(ModelResponse(parts=[])) == "parts=0"

    invalid_registry_hooks = Hooks[Any]()
    cast(Any, invalid_registry_hooks)._registry = _INVALID_HOOK_VALUE
    wrapped_hooks, changed = _wrap_hooks(invalid_registry_hooks, emitter=hidden_emitter)
    assert wrapped_hooks is invalid_registry_hooks
    assert changed is False

    bad_key_hooks = Hooks[Any]()
    cast(Any, bad_key_hooks)._registry = {1: []}
    wrapped_hooks, changed = _wrap_hooks(bad_key_hooks, emitter=hidden_emitter)
    assert wrapped_hooks is bad_key_hooks
    assert changed is False

    bad_entry_hooks = Hooks[Any]()
    cast(Any, bad_entry_hooks)._registry = {"before_run": ["bad"]}
    wrapped_hooks, changed = _wrap_hooks(bad_entry_hooks, emitter=hidden_emitter)
    assert wrapped_hooks is bad_entry_hooks
    assert changed is False

    non_callable_entry = _TestHookEntry(alpha)
    cast(Any, non_callable_entry).func = None
    wrapped_entry, changed = _wrap_hook_entry(
        "before_run",
        non_callable_entry,
        emitter=hidden_emitter,
    )
    assert wrapped_entry is non_callable_entry
    assert changed is False

    skipped_entry = _TestHookEntry(alpha)
    skipped_entry.func.__module__ = "pydantic_acp.bridges.hooks"
    wrapped_entry, changed = _wrap_hook_entry(
        "before_run",
        skipped_entry,
        emitter=hidden_emitter,
    )
    assert wrapped_entry is skipped_entry
    assert changed is False

    visible_updates: list[Any] = []

    async def visible_write(update: Any) -> None:
        visible_updates.append(update)

    visible_emitter = _HookUpdateEmitter(
        write_update=visible_write,
        projection_map=HookProjectionMap(),
        run_id="visible",
    )

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("boom")

    wrapped_error_entry, changed = _wrap_hook_entry(
        "before_tool_execute",
        _TestHookEntry(exploding),
        emitter=visible_emitter,
    )
    assert changed is True
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            cast(Any, wrapped_error_entry).func(
                cast(Any, None),
                call=ToolCallPart("echo", {"text": "ok"}),
                tool_def=cast(Any, _INVALID_HOOK_VALUE),
                args={"text": "ok"},
            )
        )
    assert any(getattr(update, "status", None) == "failed" for update in visible_updates)

    async def slow() -> None:
        await asyncio.sleep(0.01)

    with pytest.raises(HookTimeoutError):
        asyncio.run(_call_hook_func(slow, timeout=0, hook_name="before_run"))

    real_agent = Agent(TestModel(custom_output_text="observed"), capabilities=[Hooks[None]()])
    override_var = ContextVar("_override_root_capability", default=None)
    cast(Any, real_agent)._override_root_capability = override_var
    cast(Any, real_agent)._root_capability = CombinedCapability(capabilities=[])
    with observe_agent_hooks(
        real_agent,
        write_update=write_update,
        projection_map=HookProjectionMap(),
    ):
        pass


def test_wrap_hooks_preserves_hook_ordering() -> None:
    recorded_updates: list[Any] = []

    async def write_update(update: Any) -> None:
        recorded_updates.append(update)

    emitter = _HookUpdateEmitter(
        write_update=write_update,
        projection_map=HookProjectionMap(),
        run_id="ordered",
    )

    async def alpha(ctx, request_context):
        del ctx
        return request_context

    hooks = Hooks[Any](ordering=CapabilityOrdering(position="outermost"))
    cast(Any, hooks)._registry = {"before_model_request": [_TestHookEntry(alpha)]}

    wrapped_hooks, changed = _wrap_hooks(hooks, emitter=emitter)

    assert changed is True
    assert wrapped_hooks.get_ordering() == hooks.get_ordering()


def test_hook_introspection_handles_missing_registry_and_unreplaceable_entries() -> None:
    recorded_updates: list[Any] = []

    async def write_update(update: Any) -> None:
        recorded_updates.append(update)

    emitter = _HookUpdateEmitter(
        write_update=write_update,
        projection_map=HookProjectionMap(),
        run_id="hook-branches",
    )

    assert (
        asyncio.run(
            emitter.emit_progress(
                tool_call_id="hook:hook-branches:1",
                event=HookEvent(
                    event_id="before_run",
                    hook_name="progress_hidden",
                    tool_name=None,
                    tool_filters=(),
                    status=None,
                ),
            )
        )
        is None
    )
    assert recorded_updates == []

    invalid_agent = Agent(TestModel(custom_output_text="plain"))
    cast(Any, invalid_agent)._root_capability = _INVALID_HOOK_VALUE
    assert list_agent_hooks(invalid_agent) == []

    hooks = Hooks[Any]()
    cast(Any, hooks)._registry = _INVALID_HOOK_VALUE
    agent = Agent(TestModel(custom_output_text="hooked"), capabilities=[hooks])
    assert list_agent_hooks(agent) == []

    async def alpha(ctx, request_context):
        del ctx
        return request_context

    read_only_entry = _ReadOnlyHookEntry(alpha, timeout=1.0)
    wrapped_entry, changed = _wrap_hook_entry(
        "before_run",
        read_only_entry,
        emitter=emitter,
    )

    assert wrapped_entry is read_only_entry
    assert changed is False
