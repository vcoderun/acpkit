from __future__ import annotations as _annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from acp.exceptions import RequestError
from acp.schema import McpServerStdio
from pydantic_acp.bridges.base import CapabilityBridge
from pydantic_acp.runtime._agent_state import clear_selected_model_id, set_selected_model_id
from pydantic_acp.runtime._session_runtime import (
    _default_available_models,
    _known_codex_model_ids,
    _known_pydantic_model_ids,
)

from .support import (
    AdapterConfig,
    AdapterModel,
    Agent,
    MemorySessionStore,
    ModelSelectionState,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    TestModel,
    create_acp_agent,
)


def test_list_sessions_filters_by_cwd_and_close_session_handles_missing(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter_any = cast(Any, adapter)
    first = asyncio.run(adapter.new_session(cwd=str(tmp_path / "a"), mcp_servers=[]))
    second = asyncio.run(adapter.new_session(cwd=str(tmp_path / "b"), mcp_servers=[]))

    filtered = asyncio.run(adapter.list_sessions(cwd=str(tmp_path / "a")))

    assert [session.session_id for session in filtered.sessions] == [first.session_id]
    assert asyncio.run(adapter_any._session_runtime.close_session("missing")) is False
    assert asyncio.run(adapter_any._session_runtime.close_session(second.session_id)) is True
    assert asyncio.run(adapter_any._session_runtime.close_session(second.session_id)) is False


def test_session_runtime_rejects_invalid_model_and_mode_config_types(tmp_path: Path) -> None:
    agent = Agent(TestModel(custom_output_text="ok"))

    def keep_tools(_ctx: Any, tool_defs: list[Any]) -> list[Any]:
        return list(tool_defs)

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(
            available_models=[
                AdapterModel(
                    model_id="test:model",
                    name="Test Model",
                    override=cast(Any, agent.model),
                )
            ],
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="chat",
                    modes=[PrepareToolsMode(id="chat", name="Chat", prepare_func=keep_tools)],
                )
            ],
            session_store=MemorySessionStore(),
        ),
    )
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    with pytest.raises(RequestError):
        asyncio.run(adapter.set_config_option("model", session.session_id, True))
    with pytest.raises(RequestError):
        asyncio.run(adapter.set_config_option("mode", session.session_id, True))


def test_session_runtime_helper_inventory_and_missing_session_paths(tmp_path: Path) -> None:
    current_model = "openrouter:google/gemini-3-flash-preview"
    available_models = _default_available_models(
        current_model,
        current_model_value=current_model,
    )
    model_ids = [model.model_id for model in available_models]

    assert model_ids[0] == current_model
    assert len(model_ids) == len(set(model_ids))
    assert set(_known_codex_model_ids()) == {
        "codex:gpt-5.4",
        "codex:gpt-5.4-mini",
        "codex:gpt-5.3-codex",
        "codex:gpt-5.2",
    }
    assert "test" not in _known_pydantic_model_ids()

    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    adapter_any = cast(Any, adapter)
    stored_session = adapter_any._config.session_store.get(session.session_id)
    assert stored_session is not None
    adapter_any._update_session_mcp_servers(stored_session, [])
    assert stored_session.mcp_servers == []

    with pytest.raises(RequestError):
        adapter_any._session_runtime._require_session("missing")


def test_set_session_model_covers_missing_state_and_unconfigured_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter_any = cast(Any, adapter)
    runtime = adapter_any._session_runtime
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    async def no_model_state(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "_get_model_selection_state", no_model_state)
    assert asyncio.run(adapter.set_session_model("missing", session.session_id)) is None

    async def fixed_model_state(*_args: Any, **_kwargs: Any) -> ModelSelectionState:
        return ModelSelectionState(
            available_models=[],
            current_model_id="configured-model",
            allow_any_model_id=False,
        )

    monkeypatch.setattr(runtime, "_get_model_selection_state", fixed_model_state)
    with pytest.raises(RequestError):
        asyncio.run(adapter.set_session_model("missing", session.session_id))

    async def permissive_model_state(*_args: Any, **_kwargs: Any) -> ModelSelectionState:
        return ModelSelectionState(
            available_models=[],
            current_model_id="configured-model",
            allow_any_model_id=True,
        )

    async def fake_build_surface(*_args: Any, **_kwargs: Any) -> object:
        return object()

    async def fake_emit_updates(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "_get_model_selection_state", permissive_model_state)
    monkeypatch.setattr(
        runtime, "_resolve_unconfigured_model_id", lambda model_id: model_id.strip()
    )
    monkeypatch.setattr(runtime, "_remember_default_model", lambda _agent: None)
    monkeypatch.setattr(runtime, "_build_session_surface", fake_build_surface)
    monkeypatch.setattr(runtime, "_emit_session_state_updates", fake_emit_updates)

    response = asyncio.run(adapter.set_session_model(" custom:model ", session.session_id))
    stored_session = adapter_any._config.session_store.get(session.session_id)

    assert response is not None
    assert stored_session is not None
    assert stored_session.session_model_id == "custom:model"
    assert stored_session.config_values["model"] == "custom:model"


def test_set_config_option_decline_and_bridge_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BridgeConfigOptionHandler(CapabilityBridge):
        def set_config_option(
            self,
            session: Any,
            agent: Any,
            config_id: str,
            value: str | bool,
        ) -> list[Any] | None:
            del session, agent
            if config_id == "custom" and value is True:
                return []
            return None

    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            capability_bridges=[cast(Any, BridgeConfigOptionHandler())],
        ),
    )
    adapter_any = cast(Any, adapter)
    runtime = adapter_any._session_runtime
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    async def declined_model(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def declined_mode(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "set_session_model", declined_model)
    monkeypatch.setattr(runtime, "set_session_mode", declined_mode)
    assert asyncio.run(adapter.set_config_option("model", session.session_id, "x")) is None
    assert asyncio.run(adapter.set_config_option("mode", session.session_id, "chat")) is None

    async def fake_build_surface(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(config_options=[])

    async def fake_emit_updates(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "_build_session_surface", fake_build_surface)
    monkeypatch.setattr(runtime, "_emit_session_state_updates", fake_emit_updates)

    response = asyncio.run(adapter.set_config_option("custom", session.session_id, True))
    assert response is not None
    assert response.config_options == []


def test_session_runtime_misc_runtime_helpers_and_serialization(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="ok"))
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter_any = cast(Any, adapter)
    runtime = adapter_any._session_runtime
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    stored_session = adapter_any._config.session_store.get(session.session_id)

    assert stored_session is not None
    assert runtime._supports_fallback_model_selection() is True
    assert runtime._model_identity("openrouter:model") == "openrouter:model"
    assert runtime._model_identity(SimpleNamespace(model_name="named-model")) == "named-model"
    assert runtime._model_identity(SimpleNamespace(model_name=1)) is None

    with pytest.raises(RequestError):
        runtime._require_model_option("missing")

    stored_session.session_model_id = "session-model"
    assert runtime._resolve_current_model_id(stored_session, agent) == "session-model"
    stored_session.session_model_id = None
    set_selected_model_id(agent, "selected-model")
    assert runtime._resolve_current_model_id(stored_session, agent) == "selected-model"
    clear_selected_model_id(agent)
    agent.model = "fallback-model"
    assert runtime._resolve_current_model_id(stored_session, agent) == "fallback-model"

    original_mcp_servers = list(stored_session.mcp_servers)
    runtime._update_session_mcp_servers(stored_session, None)
    assert stored_session.mcp_servers == original_mcp_servers
    runtime._update_session_mcp_servers(
        stored_session,
        [McpServerStdio(name="repo", command="python", args=["-m", "server"], env=[])],
    )
    assert stored_session.mcp_servers == [
        {
            "args": ["-m", "server"],
            "command": "python",
            "name": "repo",
            "transport": "stdio",
        }
    ]

    current_model = " "
    available_models = _default_available_models(current_model, current_model_value=current_model)
    assert available_models[0].model_id != ""
