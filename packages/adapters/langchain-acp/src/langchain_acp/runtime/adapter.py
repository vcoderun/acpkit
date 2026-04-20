from __future__ import annotations as _annotations

import json
from collections.abc import Iterable
from inspect import isawaitable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from acp.exceptions import RequestError
from acp.interfaces import Agent as AcpAgent
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanUpdate,
    CloseSessionResponse,
    ForkSessionResponse,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpCapabilities,
    McpServerStdio,
    NewSessionResponse,
    PlanEntry,
    PromptCapabilities,
    PromptResponse,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionForkCapabilities,
    SessionInfo,
    SessionInfoUpdate,
    SessionListCapabilities,
    SessionModelState,
    SessionModeState,
    SessionResumeCapabilities,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

from ..approvals import ApprovalBridge
from ..bridge_manager import BridgeManager
from ..builders import GraphBridgeBuilder
from ..config import AdapterConfig
from ..event_projection import EventProjectionMap, compose_event_projection_maps
from ..graph_source import GraphSource
from ..plan import TaskPlan, _bind_native_plan_context
from ..projection import (
    ProjectionMap,
    ToolClassifier,
    build_tool_progress_update,
    build_tool_start_update,
    compose_projection_maps,
)
from ..providers import ConfigOption, ModelSelectionState, ModeState
from ..session.state import AcpSessionContext, JsonValue, StoredSessionUpdate, utc_now
from ..session.store import SessionStore
from ..types import AgentPromptBlock
from ._native_plan_runtime import _NativePlanRuntime
from ._prompt_conversion import message_text, prompt_to_langchain_content

__all__ = ("LangChainAcpAgent",)


class LangChainAcpAgent(AcpAgent):
    def __init__(self, graph_source: GraphSource, *, config: AdapterConfig) -> None:
        self._graph_source = graph_source
        self._config = config
        self._store: SessionStore = config.session_store
        self._approval_bridge: ApprovalBridge | None = config.approval_bridge
        self._native_plan_runtime = _NativePlanRuntime(self)
        self._projection_map: ProjectionMap | None = compose_projection_maps(config.projection_maps)
        self._event_projection_map: EventProjectionMap | None = compose_event_projection_maps(
            config.event_projection_maps
        )
        self._graph_bridge_builder = GraphBridgeBuilder.from_config(config)
        self._bridge_manager: BridgeManager = self._graph_bridge_builder.build_manager()
        self._tool_classifier: ToolClassifier = self._bridge_manager.tool_classifier
        self._client: AcpClient | None = None
        self._cancelled_sessions: set[str] = set()

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info, kwargs
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(
                name=self._config.agent_name,
                title=self._config.agent_title,
                version=self._config.agent_version,
            ),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                mcp_capabilities=McpCapabilities(http=True, sse=True),
                prompt_capabilities=PromptCapabilities(
                    audio=True,
                    embedded_context=True,
                    image=True,
                ),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities(),
                    fork=SessionForkCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
            auth_methods=[],
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del kwargs
        session_id = str(uuid4())
        created_at = utc_now()
        session = AcpSessionContext(
            session_id=session_id,
            cwd=Path(cwd),
            created_at=created_at,
            updated_at=created_at,
            session_model_id=self._default_model_id(),
            session_mode_id=self._default_mode_id(),
            mcp_servers=self._serialize_mcp_servers(mcp_servers),
        )
        session.session_model_id = await self._initial_model_id(session)
        session.session_mode_id = await self._initial_mode_id(session)
        self._sync_bridge_metadata(session)
        self._store.save(session)
        return NewSessionResponse(
            session_id=session_id,
            config_options=await self._config_options(session),
            models=await self._model_state(session),
            modes=await self._mode_state(session),
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        del kwargs
        session = self._store.get(session_id)
        if session is None:
            return None
        session.cwd = Path(cwd)
        session.mcp_servers = self._serialize_mcp_servers(mcp_servers)
        self._sync_bridge_metadata(session)
        await self._replay_transcript(session)
        await self._drain_bridge_updates(client=self._client, session=session)
        session.updated_at = utc_now()
        self._store.save(session)
        return LoadSessionResponse(
            config_options=await self._config_options(session),
            models=await self._model_state(session),
            modes=await self._mode_state(session),
        )

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        del cursor, kwargs
        sessions = self._store.list_sessions()
        if cwd is not None:
            sessions = [session for session in sessions if session.cwd == Path(cwd)]
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    cwd=str(session.cwd),
                    session_id=session.session_id,
                    title=session.title,
                    updated_at=session.updated_at.isoformat(),
                )
                for session in sessions
            ]
        )

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModeResponse | None:
        del kwargs
        session = self._require_session(session_id)
        resolved_mode = await self._set_mode(session, mode_id)
        if resolved_mode is None:
            raise RequestError.invalid_request({"modeId": mode_id})
        self._sync_bridge_metadata(session)
        await self._drain_bridge_updates(client=self._client, session=session)
        session.updated_at = utc_now()
        self._store.save(session)
        return SetSessionModeResponse()

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModelResponse | None:
        del kwargs
        session = self._require_session(session_id)
        resolved_model = await self._set_model(session, model_id)
        if resolved_model is None:
            raise RequestError.invalid_request({"modelId": model_id})
        self._sync_bridge_metadata(session)
        await self._drain_bridge_updates(client=self._client, session=session)
        session.updated_at = utc_now()
        self._store.save(session)
        return SetSessionModelResponse()

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        del kwargs
        session = self._require_session(session_id)
        if config_id == "model":
            if not isinstance(value, str) or await self._set_model(session, value) is None:
                raise RequestError.invalid_request({"modelId": value})
        elif config_id == "mode":
            if not isinstance(value, str) or await self._set_mode(session, value) is None:
                raise RequestError.invalid_request({"modeId": value})
        elif config_id == "plan_generation_type":
            if (
                not isinstance(value, str)
                or not self._native_plan_runtime.supports_plan_generation_selection()
                or value not in {"structured", "tools"}
            ):
                raise RequestError.invalid_request({"planGenerationType": value})
            session.config_values[config_id] = value
        else:
            session.config_values[config_id] = value
            provider_options = await self._await_maybe(
                self._bridge_manager.set_config_option(session, config_id, value)
            )
            if provider_options is not None:
                self._sync_bridge_metadata(session)
                await self._drain_bridge_updates(client=self._client, session=session)
                session.updated_at = utc_now()
                self._store.save(session)
                return SetSessionConfigOptionResponse(
                    config_options=await self._config_options(session)
                )
        self._sync_bridge_metadata(session)
        await self._drain_bridge_updates(client=self._client, session=session)
        session.updated_at = utc_now()
        self._store.save(session)
        return SetSessionConfigOptionResponse(config_options=await self._config_options(session))

    async def prompt(
        self,
        prompt: list[AgentPromptBlock],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        session = self._require_session(session_id)
        client = self._require_client()
        self._sync_bridge_metadata(session)
        graph = await self._graph_source.get_graph(session)
        graph = self._ensure_checkpointer(graph)
        await self._drain_bridge_updates(client=client, session=session)
        await self._emit_user_prompt(
            client=client, session=session, prompt=prompt, message_id=message_id
        )
        active_tool_calls: dict[str, dict[str, Any]] = {}
        tool_call_accumulator: dict[int, dict[str, str | int | None]] = {}
        decisions: list[dict[str, Any]] = []
        config = {"configurable": {"thread_id": session.session_id}}

        with _bind_native_plan_context(self._native_plan_runtime, session):
            while True:
                if session.session_id in self._cancelled_sessions:
                    self._cancelled_sessions.discard(session.session_id)
                    return PromptResponse(stop_reason="cancelled", user_message_id=message_id)

                stream_input: Command | dict[str, Any]
                if decisions:
                    stream_input = Command(resume={"decisions": decisions})
                else:
                    stream_input = {
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt_to_langchain_content(prompt),
                            }
                        ]
                    }

                should_resume = False
                async for stream_chunk in graph.astream(
                    stream_input,
                    config=config,
                    stream_mode=["messages", "updates"],
                    subgraphs=True,
                ):
                    expected_len = 3
                    if not isinstance(stream_chunk, tuple) or len(stream_chunk) != expected_len:
                        continue
                    _namespace, stream_mode, data = stream_chunk

                    if session.session_id in self._cancelled_sessions:
                        self._cancelled_sessions.discard(session.session_id)
                        return PromptResponse(stop_reason="cancelled", user_message_id=message_id)

                    if stream_mode == "updates":
                        resumed_decisions = await self._handle_update_payload(
                            client=client,
                            session=session,
                            payload=data,
                        )
                        if resumed_decisions is not None:
                            decisions = resumed_decisions
                            should_resume = True
                            break
                        await self._drain_bridge_updates(client=client, session=session)
                        continue

                    message_chunk, _metadata = data
                    await self._process_message_chunk(
                        client=client,
                        session=session,
                        message_chunk=message_chunk,
                        active_tool_calls=active_tool_calls,
                        tool_call_accumulator=tool_call_accumulator,
                    )
                    await self._drain_bridge_updates(client=client, session=session)

                if should_resume:
                    continue
                await self._drain_bridge_updates(client=client, session=session)
                return PromptResponse(stop_reason="end_turn", user_message_id=message_id)

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        del kwargs
        new_session_id = str(uuid4())
        forked = self._store.fork(session_id, new_session_id=new_session_id, cwd=Path(cwd))
        if forked is None:
            raise RequestError.resource_not_found(f"session:{session_id}")
        forked.mcp_servers = self._serialize_mcp_servers(mcp_servers)
        self._sync_bridge_metadata(forked)
        self._store.save(forked)
        return ForkSessionResponse(
            session_id=new_session_id,
            config_options=await self._config_options(forked),
            models=await self._model_state(forked),
            modes=await self._mode_state(forked),
        )

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        del kwargs
        session = self._require_session(session_id)
        session.cwd = Path(cwd)
        session.mcp_servers = self._serialize_mcp_servers(mcp_servers)
        self._sync_bridge_metadata(session)
        await self._replay_transcript(session)
        await self._drain_bridge_updates(client=self._client, session=session)
        session.updated_at = utc_now()
        self._store.save(session)
        return ResumeSessionResponse(
            config_options=await self._config_options(session),
            models=await self._model_state(session),
            modes=await self._mode_state(session),
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        del kwargs
        self._store.delete(session_id)
        return CloseSessionResponse()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        self._cancelled_sessions.add(session_id)

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        del method_id, kwargs
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del params
        raise RequestError.method_not_found(method)

    def on_connect(self, conn: AcpClient) -> None:
        self._client = conn

    async def _emit_user_prompt(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        prompt: list[AgentPromptBlock],
        message_id: str | None,
    ) -> None:
        for block in prompt:
            update = UserMessageChunk(
                session_update="user_message_chunk",
                content=block,
                message_id=message_id,
            )
            await self._emit_update(client=client, session=session, update=update)
        if session.title is None:
            title = self._derive_title(prompt)
            if title is not None:
                session.title = title
                await self._emit_update(
                    client=client,
                    session=session,
                    update=SessionInfoUpdate(
                        session_update="session_info_update",
                        title=title,
                        updated_at=utc_now().isoformat(),
                    ),
                )

    async def _process_message_chunk(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        message_chunk: Any,
        active_tool_calls: dict[str, dict[str, Any]],
        tool_call_accumulator: dict[int, dict[str, str | int | None]],
    ) -> None:
        if isinstance(message_chunk, str):
            if message_chunk:
                await self._emit_agent_text(client=client, session=session, text=message_chunk)
            return

        if isinstance(message_chunk, ToolMessage):
            await self._handle_tool_message(
                client=client,
                session=session,
                message_chunk=message_chunk,
                active_tool_calls=active_tool_calls,
            )
            return

        if isinstance(message_chunk, AIMessageChunk):
            await self._process_tool_call_chunks(
                client=client,
                session=session,
                message_chunk=message_chunk,
                active_tool_calls=active_tool_calls,
                tool_call_accumulator=tool_call_accumulator,
            )
            text = message_text(message_chunk.content)
            if text:
                await self._emit_agent_text(client=client, session=session, text=text)
            return

        content = getattr(message_chunk, "content", None)
        text = message_text(content)
        if text:
            await self._emit_agent_text(client=client, session=session, text=text)

    async def _process_tool_call_chunks(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        message_chunk: AIMessageChunk,
        active_tool_calls: dict[str, dict[str, Any]],
        tool_call_accumulator: dict[int, dict[str, str | int | None]],
    ) -> None:
        for fallback_index, chunk in enumerate(message_chunk.tool_call_chunks):
            if not isinstance(chunk, dict):
                continue
            index = chunk.get("index")
            if not isinstance(index, int):
                index = fallback_index
            state = tool_call_accumulator.setdefault(index, {"args": "", "id": None, "name": None})
            tool_call_id = chunk.get("id")
            tool_name = chunk.get("name")
            args_fragment = chunk.get("args")
            if isinstance(tool_call_id, str):
                state["id"] = tool_call_id
            if isinstance(tool_name, str):
                state["name"] = tool_name
            if isinstance(args_fragment, str):
                state["args"] = f"{cast(str, state['args'])}{args_fragment}"
            resolved_id = cast(str | None, state["id"])
            resolved_name = cast(str | None, state["name"])
            if resolved_id is None or resolved_name is None or resolved_id in active_tool_calls:
                continue
            raw_input = self._parse_json_object(cast(str, state["args"]))
            update = build_tool_start_update(
                tool_call_id=resolved_id,
                tool_name=resolved_name,
                classifier=self._tool_classifier,
                raw_input=raw_input,
                cwd=session.cwd,
                projection_map=self._projection_map,
            )
            active_tool_calls[resolved_id] = {
                "tool_name": resolved_name,
                "raw_input": raw_input,
            }
            await self._emit_update(client=client, session=session, update=update)

    async def _handle_tool_message(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        message_chunk: ToolMessage,
        active_tool_calls: dict[str, dict[str, Any]],
    ) -> None:
        tool_call_id = getattr(message_chunk, "tool_call_id", None)
        if not isinstance(tool_call_id, str):
            return
        active_tool = active_tool_calls.get(tool_call_id, {})
        tool_name = cast(str, active_tool.get("tool_name", getattr(message_chunk, "name", "tool")))
        raw_input = active_tool.get("raw_input")
        raw_output = self._projectable_raw_output(message_chunk.content)
        serialized_output = self._config.output_serializer.serialize(raw_output)
        update = build_tool_progress_update(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            classifier=self._tool_classifier,
            raw_input=raw_input,
            raw_output=raw_output,
            serialized_output=serialized_output,
            cwd=session.cwd,
            projection_map=self._projection_map,
            status=(
                "completed" if getattr(message_chunk, "status", "success") != "error" else "failed"
            ),
        )
        await self._emit_update(client=client, session=session, update=update)
        active_tool_calls.pop(tool_call_id, None)

    async def _handle_update_payload(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        payload: Any,
    ) -> list[dict[str, Any]] | None:
        if not isinstance(payload, dict):
            return None
        if "__interrupt__" in payload:
            interrupts = payload.get("__interrupt__")
            if not isinstance(interrupts, tuple | list):
                raise RequestError.invalid_request({"interrupts": interrupts})
            return await self._resolve_interrupts(
                client=client,
                session=session,
                interrupts=list(interrupts),
            )
        structured_plan = self._structured_plan_from_payload(payload)
        if (
            structured_plan is not None
            and self._native_plan_runtime.requires_structured_plan_output(session)
        ):
            await self._native_plan_runtime.persist_native_plan_state(
                session,
                entries=structured_plan.plan_entries,
                plan_markdown=structured_plan.plan_md,
            )
        await self._emit_projected_events(client=client, session=session, payload=payload)
        for update in payload.values():
            plan_entries = self._bridge_manager.extract_plan_entries(cast(JsonValue, update))
            if plan_entries is not None:
                await self._emit_plan_update(client=client, session=session, entries=plan_entries)
        return None

    async def _emit_projected_events(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        payload: dict[str, Any],
    ) -> None:
        if self._event_projection_map is None:
            return
        candidates: list[JsonValue] = [cast(JsonValue, payload)]
        seen_ids: set[int] = {id(payload)}
        for value in payload.values():
            if isinstance(value, dict) and id(value) not in seen_ids:
                candidates.append(cast(JsonValue, value))
                seen_ids.add(id(value))
        for candidate in candidates:
            projected_updates = self._event_projection_map.project_event_payload(candidate)
            if projected_updates is None:
                continue
            for projected_update in projected_updates:
                await self._emit_update(client=client, session=session, update=projected_update)

    async def _resolve_interrupts(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        interrupts: list[Any],
    ) -> list[dict[str, Any]]:
        if self._approval_bridge is None:
            raise RequestError.invalid_request({"reason": "No approval bridge is configured."})
        decisions: list[dict[str, Any]] = []
        for interrupt in interrupts:
            interrupt_value = getattr(interrupt, "value", interrupt)
            if not isinstance(interrupt_value, dict):
                raise RequestError.invalid_request({"interrupt_value": interrupt_value})
            action_requests = interrupt_value.get("action_requests")
            review_configs = interrupt_value.get("review_configs")
            if not isinstance(action_requests, list) or not isinstance(review_configs, list):
                raise RequestError.invalid_request({"interrupt_value": interrupt_value})
            resolution = await self._approval_bridge.resolve_action_requests(
                client=client,
                session=session,
                action_requests=cast(list[dict[str, Any]], action_requests),
                review_configs=cast(list[dict[str, Any]], review_configs),
                classifier=self._tool_classifier,
            )
            if resolution.cancelled:
                raise RequestError.invalid_request({"reason": "Prompt cancelled during approval."})
            decisions.extend(resolution.decisions)
        return decisions

    async def _emit_plan_update(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        entries: list[PlanEntry],
    ) -> None:
        if self._native_plan_runtime.supports_native_plan_state(session):
            await self._native_plan_runtime.persist_native_plan_state(
                session,
                entries=entries,
                plan_markdown=session.plan_markdown,
            )
            return
        session.plan_entries = [
            entry.model_dump(mode="json", exclude_none=True) for entry in entries
        ]
        update = AgentPlanUpdate(session_update="plan", entries=entries)
        await self._emit_update(client=client, session=session, update=update)

    async def _emit_agent_text(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        text: str,
        message_id: str | None = None,
    ) -> None:
        if not text:
            return
        await self._emit_update(
            client=client,
            session=session,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
                message_id=message_id,
            ),
        )

    async def _emit_update(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        update: Any,
    ) -> None:
        await client.session_update(session_id=session.session_id, update=update)
        if isinstance(
            update,
            AgentMessageChunk
            | AgentPlanUpdate
            | SessionInfoUpdate
            | ToolCallProgress
            | ToolCallStart
            | UserMessageChunk,
        ):
            session.transcript.append(StoredSessionUpdate.from_update(update))
        session.updated_at = utc_now()
        self._store.save(session)

    async def _replay_transcript(self, session: AcpSessionContext) -> None:
        if not self._config.replay_history_on_load or self._client is None:
            return
        for stored_update in session.transcript:
            await self._client.session_update(
                session_id=session.session_id,
                update=stored_update.to_update(),
            )

    def _parse_json_object(self, value: str) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _projectable_raw_output(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        return parsed

    async def _config_options(self, session: AcpSessionContext) -> list[ConfigOption]:
        options: list[ConfigOption] = []
        mode_state = await self._resolved_mode_selection_state(session)
        if mode_state is not None and mode_state.enable_config_option:
            current_mode_id = mode_state.current_mode_id
            if current_mode_id is not None:
                options.append(
                    SessionConfigOptionSelect(
                        id="mode",
                        name=mode_state.config_option_name,
                        type="select",
                        current_value=current_mode_id,
                        options=[
                            SessionConfigSelectOption(name=mode.name, value=mode.id)
                            for mode in mode_state.modes
                        ],
                    )
                )
        model_state = await self._resolved_model_selection_state(session)
        if model_state is not None and model_state.enable_config_option:
            current_model_id = model_state.current_model_id
            if current_model_id is not None:
                options.append(
                    SessionConfigOptionSelect(
                        id="model",
                        name=model_state.config_option_name,
                        type="select",
                        current_value=current_model_id,
                        options=[
                            SessionConfigSelectOption(name=model.name, value=model.model_id)
                            for model in model_state.available_models
                        ],
                    )
                )
        bridge_options = await self._await_maybe(self._bridge_manager.get_config_options(session))
        if bridge_options is not None:
            options.extend(bridge_options)
        options.extend(await self._native_plan_runtime.config_options(session))
        return options

    async def _model_state(self, session: AcpSessionContext) -> SessionModelState | None:
        model_state = await self._resolved_model_selection_state(session)
        if model_state is None or model_state.current_model_id is None:
            return None
        return SessionModelState(
            available_models=list(model_state.available_models),
            current_model_id=model_state.current_model_id,
        )

    async def _mode_state(self, session: AcpSessionContext) -> SessionModeState | None:
        mode_state = await self._resolved_mode_selection_state(session)
        if mode_state is None or mode_state.current_mode_id is None:
            return None
        return SessionModeState(
            available_modes=list(mode_state.modes),
            current_mode_id=mode_state.current_mode_id,
        )

    def _default_model_id(self) -> str | None:
        if self._config.default_model_id is not None:
            return self._config.default_model_id
        if self._config.available_models:
            return self._config.available_models[0].model_id
        return None

    def _default_mode_id(self) -> str | None:
        if self._config.default_mode_id is not None:
            return self._config.default_mode_id
        if self._config.available_modes:
            return self._config.available_modes[0].id
        return None

    def _model_exists(self, model_id: str) -> bool:
        return any(model.model_id == model_id for model in self._config.available_models)

    def _mode_exists(self, mode_id: str) -> bool:
        return any(mode.id == mode_id for mode in self._config.available_modes)

    def _require_session(self, session_id: str) -> AcpSessionContext:
        session = self._store.get(session_id)
        if session is None:
            raise RequestError.resource_not_found(f"session:{session_id}")
        if self._client is not None:
            session.client = self._client
        return session

    def _require_client(self) -> AcpClient:
        if self._client is None:
            raise RequestError.invalid_request({"reason": "No ACP client is connected."})
        return self._client

    def _serialize_mcp_servers(
        self,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None,
    ) -> list[dict[str, JsonValue]]:
        if mcp_servers is None:
            return []
        return [
            cast(
                dict[str, JsonValue],
                server.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
            for server in mcp_servers
        ]

    def _derive_title(self, prompt: Iterable[AgentPromptBlock]) -> str | None:
        for block in prompt:
            if isinstance(block, TextContentBlock):
                text = block.text.strip()
                if not text:
                    continue
                return text[:80]
        return None

    def _ensure_checkpointer(self, graph: Any) -> Any:
        if getattr(graph, "checkpointer", None) is not None:
            return graph
        builder = getattr(graph, "builder", None)
        if builder is None or not hasattr(builder, "compile"):
            return graph
        return builder.compile(checkpointer=MemorySaver(), name=getattr(graph, "name", None))

    async def _resolved_model_selection_state(
        self, session: AcpSessionContext
    ) -> ModelSelectionState | None:
        return await self._await_maybe(self._bridge_manager.get_model_state(session))

    async def _resolved_mode_selection_state(self, session: AcpSessionContext) -> ModeState | None:
        return await self._await_maybe(self._bridge_manager.get_mode_state(session))

    async def _initial_model_id(self, session: AcpSessionContext) -> str | None:
        model_state = await self._resolved_model_selection_state(session)
        if model_state is None:
            return session.session_model_id
        return model_state.current_model_id

    async def _initial_mode_id(self, session: AcpSessionContext) -> str | None:
        mode_state = await self._resolved_mode_selection_state(session)
        if mode_state is None:
            return session.session_mode_id
        return mode_state.current_mode_id

    async def _set_model(
        self, session: AcpSessionContext, model_id: str
    ) -> ModelSelectionState | None:
        return await self._await_maybe(self._bridge_manager.set_model(session, model_id))

    async def _set_mode(self, session: AcpSessionContext, mode_id: str) -> ModeState | None:
        return await self._await_maybe(self._bridge_manager.set_mode(session, mode_id))

    async def _await_maybe(self, value: Any) -> Any:
        if isawaitable(value):
            return await value
        return value

    def _structured_plan_from_payload(self, payload: dict[str, Any]) -> TaskPlan | None:
        for value in payload.values():
            task_plan = self._task_plan_from_value(value)
            if task_plan is not None:
                return task_plan
        return None

    def _task_plan_from_value(self, value: Any) -> TaskPlan | None:
        if isinstance(value, TaskPlan):
            return value
        if not isinstance(value, dict):
            return None
        structured_response = value.get("structured_response")
        if isinstance(structured_response, BaseModel):
            candidate: Any = structured_response.model_dump(mode="python")
        else:
            candidate = structured_response
        if candidate is None:
            return None
        try:
            return TaskPlan.model_validate(candidate)
        except ValidationError:
            return None

    def _sync_bridge_metadata(self, session: AcpSessionContext) -> None:
        metadata = dict(session.metadata)
        for metadata_key in self._bridge_manager.metadata_keys:
            metadata.pop(metadata_key, None)
        metadata.update(self._bridge_manager.get_metadata_sections(session))
        session.metadata = metadata

    async def _drain_bridge_updates(
        self,
        *,
        client: AcpClient | None,
        session: AcpSessionContext,
    ) -> None:
        if client is None:
            return
        while True:
            bridge_updates = self._bridge_manager.drain_updates(session)
            if not bridge_updates:
                return
            for update in bridge_updates:
                await self._emit_update(client=client, session=session, update=update)
