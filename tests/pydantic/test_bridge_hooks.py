from __future__ import annotations as _annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from typing_extensions import Sentinel

from .support import (
    UTC,
    AcpSessionContext,
    Agent,
    ExternalHookEventBridge,
    HookBridge,
    HookEvent,
    HookProjectionMap,
    Path,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    ToolDefinition,
    datetime,
)

_HOOK_CONTEXT = Sentinel("_HOOK_CONTEXT")


def _registered_event_hook(
    registry: Mapping[str, list[Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    for key in ("on_event", "_on_event"):
        if entries := registry.get(key):
            return cast("Callable[..., Coroutine[Any, Any, Any]]", entries[0].func)
    raise AssertionError("Hooks capability did not register an event callback")


def test_hook_bridge_error_paths_emit_failed_updates() -> None:
    bridge = HookBridge()
    session = AcpSessionContext(
        session_id="hook-errors",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    capability = bridge.build_capability(session)
    registry = capability._registry
    tool_call = ToolCallPart("echo", {"text": "hello"})
    tool_def = ToolDefinition(name="echo")

    async def fail_run() -> Any:
        raise RuntimeError("run failed")

    async def fail_node(node: Any) -> Any:
        del node
        raise RuntimeError("node failed")

    async def fail_model(request_context: Any) -> ModelResponse:
        del request_context
        raise RuntimeError("model failed")

    async def fail_validate(args: Any) -> Any:
        del args
        raise RuntimeError("validate failed")

    async def fail_execute(args: Any) -> Any:
        del args
        raise RuntimeError("execute failed")

    with pytest.raises(RuntimeError, match="run failed"):
        asyncio.run(registry["wrap_run"][0].func(cast("Any", None), handler=fail_run))
    with pytest.raises(RuntimeError, match="run direct"):
        asyncio.run(
            registry["on_run_error"][0].func(cast("Any", None), error=RuntimeError("run direct")),
        )
    with pytest.raises(RuntimeError, match="node failed"):
        asyncio.run(
            registry["wrap_node_run"][0].func(
                cast("Any", None),
                node=_HOOK_CONTEXT,
                handler=fail_node,
            ),
        )
    with pytest.raises(RuntimeError, match="model failed"):
        asyncio.run(
            registry["wrap_model_request"][0].func(
                cast("Any", None),
                request_context=_HOOK_CONTEXT,
                handler=fail_model,
            ),
        )
    with pytest.raises(RuntimeError, match="validate failed"):
        asyncio.run(
            registry["wrap_tool_validate"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                handler=fail_validate,
            ),
        )
    with pytest.raises(RuntimeError, match="execute failed"):
        asyncio.run(
            registry["wrap_tool_execute"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                handler=fail_execute,
            ),
        )
    with pytest.raises(RuntimeError, match="execute direct"):
        asyncio.run(
            registry["on_tool_execute_error"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                error=RuntimeError("execute direct"),
            ),
        )

    updates = bridge.drain_updates(session, Agent(TestModel(custom_output_text="unused")))
    assert updates is not None
    failed_titles = [
        update.title
        for update in updates
        if isinstance(update, ToolCallProgress) and update.status == "failed"
    ]
    assert failed_titles == [
        "hook.wrap_run",
        "hook.on_run_error",
        "hook.wrap_node_run",
        "hook.wrap_model_request",
        "hook.wrap_tool_validate",
        "hook.wrap_tool_execute",
        "hook.on_tool_execute_error",
    ]


def test_hook_bridge_success_paths_and_disabled_metadata() -> None:
    session = AcpSessionContext(
        session_id="hook-success",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    bridge = HookBridge()
    capability = bridge.build_capability(session)
    registry = capability._registry
    tool_call = ToolCallPart("echo", {"text": "hello"})
    tool_def = ToolDefinition(name="echo")
    response = ModelResponse(parts=[])

    async def ok_run() -> Any:
        return cast("Any", "run-result")

    async def ok_node(node: Any) -> Any:
        return node

    async def ok_model(request_context: Any) -> ModelResponse:
        del request_context
        return response

    async def ok_validate(args: Any) -> Any:
        return args

    async def ok_execute(args: Any) -> Any:
        return {"ok": args["text"]}

    async def ok_output(output: Any) -> Any:
        return output

    async def fail_output(output: Any) -> Any:
        del output
        raise RuntimeError("output failed")

    async def event_stream():
        yield cast("Any", SimpleNamespace(event_kind="demo_event"))

    request_context = cast("Any", SimpleNamespace(messages=[1, 2]))
    assert asyncio.run(registry["before_run"][0].func(cast("Any", None))) is None
    assert (
        asyncio.run(registry["wrap_run"][0].func(cast("Any", None), handler=ok_run)) == "run-result"
    )
    assert (
        asyncio.run(registry["after_run"][0].func(cast("Any", None), result=cast("Any", "done")))
        == "done"
    )
    assert (
        asyncio.run(registry["before_node_run"][0].func(cast("Any", None), node=tool_call))
        is tool_call
    )
    assert (
        asyncio.run(
            registry["after_node_run"][0].func(
                cast("Any", None),
                node=tool_call,
                result=cast("Any", "node-result"),
            ),
        )
        == "node-result"
    )
    assert (
        asyncio.run(
            registry["wrap_node_run"][0].func(
                cast("Any", None),
                node=tool_call,
                handler=ok_node,
            ),
        )
        is tool_call
    )
    assert (
        asyncio.run(_registered_event_hook(registry)(cast("Any", None), cast("Any", tool_call)))
        is tool_call
    )
    wrapped_stream = registry["wrap_run_event_stream"][0].func(
        cast("Any", None),
        stream=event_stream(),
    )
    assert asyncio.run(wrapped_stream.__anext__()).event_kind == "demo_event"
    assert (
        asyncio.run(registry["before_model_request"][0].func(cast("Any", None), request_context))
        is request_context
    )
    assert (
        asyncio.run(
            registry["wrap_model_request"][0].func(
                cast("Any", None),
                request_context=request_context,
                handler=ok_model,
            ),
        )
        is response
    )
    assert (
        asyncio.run(
            registry["after_model_request"][0].func(
                cast("Any", None),
                request_context=request_context,
                response=response,
            ),
        )
        is response
    )
    assert asyncio.run(registry["prepare_tools"][0].func(cast("Any", None), [tool_def])) == [
        tool_def,
    ]
    assert asyncio.run(
        registry["before_tool_validate"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        registry["after_tool_validate"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        registry["wrap_tool_validate"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
            handler=ok_validate,
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        registry["before_tool_execute"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        registry["wrap_tool_execute"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
            handler=ok_execute,
        ),
    ) == {"ok": "hello"}
    assert (
        asyncio.run(
            registry["after_tool_execute"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                result="done",
            ),
        )
        == "done"
    )

    updates = bridge.drain_updates(session, Agent(TestModel(custom_output_text="unused")))
    assert updates is not None
    assert len([u for u in updates if isinstance(u, ToolCallProgress)]) >= 15

    disabled = HookBridge(
        record_event_stream=False,
        record_model_requests=False,
        record_node_lifecycle=False,
        record_deferred_tool_calls=False,
        record_output_processing=False,
        record_output_validation=False,
        record_prepare_output_tools=False,
        record_prepare_tools=False,
        record_run_lifecycle=False,
        record_tool_execution=False,
        record_tool_validation=False,
    )
    disabled_capability = disabled.build_capability(session)
    assert disabled_capability._registry == {}
    assert disabled.get_session_metadata(session, Agent(TestModel())) == {"events": []}

    partially_disabled = HookBridge()
    partially_capability = partially_disabled.build_capability(session)
    partially_registry = partially_capability._registry
    partially_disabled.record_run_lifecycle = False
    partially_disabled.record_node_lifecycle = False
    partially_disabled.record_event_stream = False
    partially_disabled.record_model_requests = False
    partially_disabled.record_deferred_tool_calls = False
    partially_disabled.record_output_processing = False
    partially_disabled.record_output_validation = False
    partially_disabled.record_prepare_output_tools = False
    partially_disabled.record_prepare_tools = False
    partially_disabled.record_tool_validation = False
    partially_disabled.record_tool_execution = False

    assert asyncio.run(partially_registry["before_run"][0].func(cast("Any", None))) is None
    assert (
        asyncio.run(
            partially_registry["before_node_run"][0].func(cast("Any", None), node=tool_call),
        )
        is tool_call
    )
    assert (
        asyncio.run(
            _registered_event_hook(partially_registry)(
                cast("Any", None),
                cast("Any", SimpleNamespace(event_kind="silent")),
            ),
        ).event_kind
        == "silent"
    )
    assert (
        asyncio.run(
            partially_registry["before_model_request"][0].func(cast("Any", None), request_context),
        )
        is request_context
    )
    assert asyncio.run(
        partially_registry["prepare_tools"][0].func(cast("Any", None), [tool_def]),
    ) == [tool_def]
    assert asyncio.run(
        partially_registry["prepare_output_tools"][0].func(cast("Any", None), [tool_def]),
    ) == [tool_def]
    assert (
        asyncio.run(
            partially_registry["before_output_validate"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
            ),
        )
        == "raw"
    )
    assert (
        asyncio.run(
            partially_registry["wrap_output_validate"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                handler=ok_output,
            ),
        )
        == "raw"
    )
    with pytest.raises(RuntimeError, match="output failed"):
        asyncio.run(
            partially_registry["wrap_output_validate"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                handler=fail_output,
            ),
        )
    assert (
        asyncio.run(
            partially_registry["after_output_validate"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="checked",
            ),
        )
        == "checked"
    )
    with pytest.raises(RuntimeError, match="validate failed"):
        asyncio.run(
            partially_registry["on_output_validate_error"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                error=RuntimeError("validate failed"),
            ),
        )
    assert (
        asyncio.run(
            partially_registry["before_output_process"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
            ),
        )
        == "raw"
    )
    assert (
        asyncio.run(
            partially_registry["wrap_output_process"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                handler=ok_output,
            ),
        )
        == "raw"
    )
    with pytest.raises(RuntimeError, match="output failed"):
        asyncio.run(
            partially_registry["wrap_output_process"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                handler=fail_output,
            ),
        )
    assert (
        asyncio.run(
            partially_registry["after_output_process"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="processed",
            ),
        )
        == "processed"
    )
    with pytest.raises(RuntimeError, match="process failed"):
        asyncio.run(
            partially_registry["on_output_process_error"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                error=RuntimeError("process failed"),
            ),
        )
    assert (
        asyncio.run(
            partially_registry["handle_deferred_tool_calls"][0].func(
                cast("Any", None),
                requests=cast("Any", None),
            ),
        )
        is None
    )
    assert asyncio.run(
        partially_registry["before_tool_validate"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        partially_registry["before_tool_execute"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert partially_disabled.drain_updates(session, Agent(TestModel())) is None


def test_hook_bridge_hide_all_disables_registry_and_metadata() -> None:
    bridge = HookBridge(hide_all=True)
    session = AcpSessionContext(
        session_id="hook-hidden",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    capability = bridge.build_capability(session)

    assert capability._registry == {}
    assert bridge.get_session_metadata(session, Agent(TestModel())) == {"events": []}
    assert bridge.drain_updates(session, Agent(TestModel())) is None


def test_hook_bridge_records_output_and_deferred_hooks() -> None:
    bridge = HookBridge()
    session = AcpSessionContext(
        session_id="hook-output",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    registry = bridge.build_capability(session)._registry
    tool_def = ToolDefinition(name="result")

    async def ok_output(output: Any) -> Any:
        return output

    assert asyncio.run(registry["prepare_output_tools"][0].func(cast("Any", None), [tool_def])) == [
        tool_def,
    ]
    assert (
        asyncio.run(
            registry["before_output_validate"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
            ),
        )
        == "raw"
    )
    assert (
        asyncio.run(
            registry["wrap_output_validate"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                handler=ok_output,
            ),
        )
        == "raw"
    )
    assert asyncio.run(
        registry["after_output_validate"][0].func(
            cast("Any", None),
            output_context=_HOOK_CONTEXT,
            output={"ok": True},
        ),
    ) == {"ok": True}
    assert asyncio.run(
        registry["before_output_process"][0].func(
            cast("Any", None),
            output_context=_HOOK_CONTEXT,
            output={"ok": True},
        ),
    ) == {"ok": True}
    assert asyncio.run(
        registry["wrap_output_process"][0].func(
            cast("Any", None),
            output_context=_HOOK_CONTEXT,
            output={"ok": True},
            handler=ok_output,
        ),
    ) == {"ok": True}
    assert (
        asyncio.run(
            registry["after_output_process"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="done",
            ),
        )
        == "done"
    )
    assert (
        asyncio.run(
            registry["handle_deferred_tool_calls"][0].func(
                cast("Any", None),
                requests=cast("Any", None),
            ),
        )
        is None
    )

    updates = bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None
    titles = [update.title for update in updates if isinstance(update, ToolCallProgress)]
    assert "hook.prepare_output_tools" in titles
    assert "hook.wrap_output_validate" in titles
    assert "hook.wrap_output_process" in titles
    assert "hook.handle_deferred_tool_calls" in titles


def test_hook_bridge_output_error_hooks_emit_failed_updates() -> None:
    bridge = HookBridge()
    session = AcpSessionContext(
        session_id="hook-output-errors",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    registry = bridge.build_capability(session)._registry

    async def fail_output(output: Any) -> Any:
        del output
        raise RuntimeError("output failed")

    with pytest.raises(RuntimeError, match="output failed"):
        asyncio.run(
            registry["wrap_output_validate"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                handler=fail_output,
            ),
        )
    with pytest.raises(RuntimeError, match="validate direct"):
        asyncio.run(
            registry["on_output_validate_error"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                error=RuntimeError("validate direct"),
            ),
        )
    with pytest.raises(RuntimeError, match="output failed"):
        asyncio.run(
            registry["wrap_output_process"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                handler=fail_output,
            ),
        )
    with pytest.raises(RuntimeError, match="process direct"):
        asyncio.run(
            registry["on_output_process_error"][0].func(
                cast("Any", None),
                output_context=_HOOK_CONTEXT,
                output="raw",
                error=RuntimeError("process direct"),
            ),
        )

    updates = bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None
    failed_titles = [
        update.title
        for update in updates
        if isinstance(update, ToolCallProgress) and update.status == "failed"
    ]
    assert failed_titles == [
        "hook.wrap_output_validate",
        "hook.on_output_validate_error",
        "hook.wrap_output_process",
        "hook.on_output_process_error",
    ]


def test_hook_bridge_skips_recording_when_flags_are_disabled_after_binding() -> None:
    bridge = HookBridge()
    session = AcpSessionContext(
        session_id="hook-disabled-paths",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    capability = bridge.build_capability(session)
    registry = capability._registry
    tool_call = ToolCallPart("echo", {"text": "hello"})
    tool_def = ToolDefinition(name="echo")
    request_context = cast("Any", SimpleNamespace(messages=[1]))
    response = ModelResponse(parts=[])

    bridge.record_run_lifecycle = False
    bridge.record_node_lifecycle = False
    bridge.record_event_stream = False
    bridge.record_model_requests = False
    bridge.record_prepare_tools = False
    bridge.record_tool_validation = False
    bridge.record_tool_execution = False

    async def ok_run() -> Any:
        return cast("Any", "run-result")

    async def fail_run() -> Any:
        raise RuntimeError("run failed")

    async def ok_node(node: Any) -> Any:
        return node

    async def fail_node(node: Any) -> Any:
        del node
        raise RuntimeError("node failed")

    async def ok_model(request_context: Any) -> ModelResponse:
        del request_context
        return response

    async def fail_model(request_context: Any) -> ModelResponse:
        del request_context
        raise RuntimeError("model failed")

    async def ok_validate(args: Any) -> Any:
        return args

    async def fail_validate(args: Any) -> Any:
        del args
        raise RuntimeError("validate failed")

    async def ok_execute(args: Any) -> Any:
        return {"ok": args["text"]}

    async def fail_execute(args: Any) -> Any:
        del args
        raise RuntimeError("execute failed")

    async def event_stream():
        yield cast("Any", SimpleNamespace(event_kind="demo_event"))

    assert asyncio.run(registry["before_run"][0].func(cast("Any", None))) is None
    assert (
        asyncio.run(registry["wrap_run"][0].func(cast("Any", None), handler=ok_run)) == "run-result"
    )
    with pytest.raises(RuntimeError, match="run failed"):
        asyncio.run(registry["wrap_run"][0].func(cast("Any", None), handler=fail_run))
    assert asyncio.run(registry["after_run"][0].func(cast("Any", None), result="done")) == "done"
    with pytest.raises(RuntimeError, match="run direct"):
        asyncio.run(
            registry["on_run_error"][0].func(cast("Any", None), error=RuntimeError("run direct")),
        )
    assert (
        asyncio.run(registry["before_node_run"][0].func(cast("Any", None), node=tool_call))
        is tool_call
    )
    assert (
        asyncio.run(
            registry["after_node_run"][0].func(
                cast("Any", None),
                node=tool_call,
                result="node-result",
            ),
        )
        == "node-result"
    )
    assert (
        asyncio.run(
            registry["wrap_node_run"][0].func(cast("Any", None), node=tool_call, handler=ok_node),
        )
        is tool_call
    )
    with pytest.raises(RuntimeError, match="node failed"):
        asyncio.run(
            registry["wrap_node_run"][0].func(cast("Any", None), node=tool_call, handler=fail_node),
        )
    assert (
        asyncio.run(_registered_event_hook(registry)(cast("Any", None), cast("Any", tool_call)))
        is tool_call
    )
    wrapped_stream = registry["wrap_run_event_stream"][0].func(
        cast("Any", None),
        stream=event_stream(),
    )
    assert asyncio.run(wrapped_stream.__anext__()).event_kind == "demo_event"
    assert (
        asyncio.run(registry["before_model_request"][0].func(cast("Any", None), request_context))
        is request_context
    )
    assert (
        asyncio.run(
            registry["wrap_model_request"][0].func(
                cast("Any", None),
                request_context=request_context,
                handler=ok_model,
            ),
        )
        is response
    )
    with pytest.raises(RuntimeError, match="model failed"):
        asyncio.run(
            registry["wrap_model_request"][0].func(
                cast("Any", None),
                request_context=request_context,
                handler=fail_model,
            ),
        )
    assert (
        asyncio.run(
            registry["after_model_request"][0].func(
                cast("Any", None),
                request_context=request_context,
                response=response,
            ),
        )
        is response
    )
    assert asyncio.run(registry["prepare_tools"][0].func(cast("Any", None), [tool_def])) == [
        tool_def,
    ]
    assert asyncio.run(
        registry["before_tool_validate"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        registry["after_tool_validate"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        registry["wrap_tool_validate"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
            handler=ok_validate,
        ),
    ) == {"text": "hello"}
    with pytest.raises(RuntimeError, match="validate failed"):
        asyncio.run(
            registry["wrap_tool_validate"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                handler=fail_validate,
            ),
        )
    assert asyncio.run(
        registry["before_tool_execute"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
        ),
    ) == {"text": "hello"}
    assert asyncio.run(
        registry["wrap_tool_execute"][0].func(
            cast("Any", None),
            call=tool_call,
            tool_def=tool_def,
            args={"text": "hello"},
            handler=ok_execute,
        ),
    ) == {"ok": "hello"}
    with pytest.raises(RuntimeError, match="execute failed"):
        asyncio.run(
            registry["wrap_tool_execute"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                handler=fail_execute,
            ),
        )
    assert (
        asyncio.run(
            registry["after_tool_execute"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                result="done",
            ),
        )
        == "done"
    )
    with pytest.raises(RuntimeError, match="execute direct"):
        asyncio.run(
            registry["on_tool_execute_error"][0].func(
                cast("Any", None),
                call=tool_call,
                tool_def=tool_def,
                args={"text": "hello"},
                error=RuntimeError("execute direct"),
            ),
        )

    assert bridge.drain_updates(session, Agent(TestModel(custom_output_text="unused"))) is None


def test_external_hook_event_bridge_records_modes_and_metadata() -> None:
    session = AcpSessionContext(
        session_id="external-hooks",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    bridge = ExternalHookEventBridge()
    event = HookEvent(
        event_id="before_run",
        hook_name="before_run",
        tool_name=None,
        tool_filters=(),
        raw_output="done",
        status="completed",
    )

    bridge.record_event(session, event)
    metadata = bridge.get_session_metadata(session, Agent(TestModel()))
    updates = bridge.drain_updates(session, Agent(TestModel()))

    assert metadata["pending_event_count"] == 2
    assert updates is not None
    assert [update.tool_call_id for update in updates] == [
        "external-hooks:external-hook:1",
        "external-hooks:external-hook:1",
    ]
    assert isinstance(updates[0], ToolCallStart)
    assert isinstance(updates[1], ToolCallProgress)
    assert updates[1].status == "completed"
    assert bridge.drain_updates(session, Agent(TestModel())) is None


def test_external_hook_event_bridge_start_only_and_hidden_events() -> None:
    session = AcpSessionContext(
        session_id="external-hooks-hidden",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    bridge = ExternalHookEventBridge(
        projection_map=HookProjectionMap(hidden_event_ids=frozenset({"secret"})),
        emission_mode="start_only",
    )

    bridge.record_event(
        session,
        HookEvent(
            event_id="secret",
            hook_name="secret",
            tool_name=None,
            tool_filters=(),
            status="completed",
        ),
    )
    bridge.record_event(
        session,
        HookEvent(
            event_id="before_run",
            hook_name="before_run",
            tool_name=None,
            tool_filters=(),
            status="failed",
        ),
    )

    updates = bridge.drain_updates(session, Agent(TestModel()))

    assert updates is not None
    assert len(updates) == 1
    assert isinstance(updates[0], ToolCallStart)
    assert updates[0].status == "failed"


def test_external_hook_event_bridge_start_only_preserves_in_progress_without_status() -> None:
    session = AcpSessionContext(
        session_id="external-hooks-start-only-default-status",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    bridge = ExternalHookEventBridge(emission_mode="start_only")

    bridge.record_event(
        session,
        HookEvent(
            event_id="before_run",
            hook_name="before_run",
            tool_name=None,
            tool_filters=(),
            status=None,
        ),
    )

    updates = bridge.drain_updates(session, Agent(TestModel()))

    assert updates is not None
    assert len(updates) == 1
    assert isinstance(updates[0], ToolCallStart)
    assert updates[0].status == "in_progress"


def test_external_hook_event_bridge_keeps_start_when_progress_is_hidden() -> None:
    session = AcpSessionContext(
        session_id="external-hooks-start-only-progress-hidden",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    bridge = ExternalHookEventBridge()

    bridge.record_event(
        session,
        HookEvent(
            event_id="before_run",
            hook_name="before_run",
            tool_name=None,
            tool_filters=(),
            status=None,
        ),
    )

    updates = bridge.drain_updates(session, Agent(TestModel()))

    assert updates is not None
    assert len(updates) == 1
    assert isinstance(updates[0], ToolCallStart)
    assert updates[0].status == "in_progress"
