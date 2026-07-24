from __future__ import annotations as _annotations

import asyncio
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, TypeAlias, cast
from unittest.mock import AsyncMock

import pydantic_acp
import pydantic_acp.command_agent as command_agent_module
import pytest
from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.helpers import text_block
from acp.interfaces import Agent as AcpAgent
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AcceptElicitationResponse,
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    AuthEnvVar,
    AuthMethodAgent,
    ClientCapabilities,
    ElicitationFormSessionMode,
    ElicitationMode,
    ElicitationSchema,
    EnvVarAuthMethod,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
    TerminalAuthMethod,
    ToolCallUpdate,
    Usage,
    UsageUpdate,
)
from pydantic import BaseModel
from pydantic_acp import AcpHostBridge, AcpModel, AcpProvider, create_acp_agent, create_acp_model
from pydantic_acp import client as client_module
from pydantic_acp._meta_protocol import (
    MISSING_STRUCTURED_OUTPUT,
    build_structured_output_request_meta,
    build_structured_output_response_meta,
    build_structured_output_type,
    extract_field_meta,
    extract_structured_output,
    has_structured_output_request,
)
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    InstructionPart,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UploadedFile,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.providers import Provider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from .support import HostRecordingClient, RecordingClient

TestAuthMethod: TypeAlias = EnvVarAuthMethod | TerminalAuthMethod | AuthMethodAgent


class EchoACPAgent:  # type: ignore[misc]
    def __init__(
        self,
        *,
        stop_reason: Literal[
            "end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"
        ] = "end_turn",
        usage: Usage | None = None,
    ) -> None:
        self.client: Any | None = None
        self.initialized_protocols: list[int] = []
        self.session_cwds: list[str] = []
        self.session_models: list[tuple[str, str]] = []
        self.prompts: list[tuple[str, str]] = []
        self.stop_reason: Literal[
            "end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"
        ] = stop_reason
        self.usage = usage
        self.client_capabilities: ClientCapabilities | None = None
        self.client_info: Implementation | None = None
        self.mcp_servers_seen: list[list[Any] | None] = []

    def on_connect(self, conn: Any) -> None:
        self.client = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del kwargs
        self.initialized_protocols.append(protocol_version)
        self.client_capabilities = client_capabilities
        self.client_info = client_info
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(name="echo-acp-agent", version="test"),
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del kwargs
        self.session_cwds.append(cwd)
        self.mcp_servers_seen.append(mcp_servers)
        return NewSessionResponse(
            session_id=f"session-{len(self.session_cwds)}",
            config_options=[
                SessionConfigOptionSelect(
                    id="model",
                    name="Model",
                    category="agent",
                    type="select",
                    current_value="agent",
                    options=[SessionConfigSelectOption(value="agent", name="Agent")],
                ),
            ],
        )

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        del kwargs
        if config_id == "model" and isinstance(value, str):  # pragma: no branch
            self.session_models.append((session_id, value))
        return SetSessionConfigOptionResponse(config_options=[])

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        del kwargs
        self.session_models.append((session_id, model_id))

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del message_id, kwargs
        if self.client is None:
            raise AssertionError("ACP agent was not connected to a host client")
        rendered_prompt = "".join(str(getattr(block, "text", "")) for block in prompt)
        self.prompts.append((session_id, rendered_prompt))
        await self.client.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=text_block(f"acp echo: {rendered_prompt}"),
            ),
            source="echo-acp-agent",
        )
        return PromptResponse(stop_reason=self.stop_reason, usage=self.usage)


def _build_provider_and_model(
    acp_agent: Any,
    *,
    model_name: str = "zed-agent",
    cwd: str = "/workspace",
    prompt_renderer: Any = None,
) -> tuple[AcpProvider, AcpModel]:
    """Construct an ``AcpProvider``/``AcpModel`` pair with this file's shared test defaults."""
    provider = AcpProvider(acp_agent=acp_agent, cwd=cwd, prompt_renderer=prompt_renderer)
    model = AcpModel(model_name=model_name, provider=provider)
    return provider, model


class SetConfigOptionErrorACPAgent(EchoACPAgent):
    def __init__(self, error: RequestError) -> None:
        super().__init__()
        self.error = error

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        del config_id, session_id, value, kwargs
        raise self.error


class DelayedUpdateACPAgent(EchoACPAgent):
    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del message_id, kwargs
        if self.client is None:
            raise AssertionError("ACP agent was not connected to a host client")
        rendered_prompt = "".join(str(getattr(block, "text", "")) for block in prompt)
        self.prompts.append((session_id, rendered_prompt))

        async def publish_update() -> None:
            await asyncio.sleep(0)
            assert self.client is not None
            await self.client.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=text_block(f"delayed acp echo: {rendered_prompt}"),
                ),
                source="delayed-update-agent",
            )

        asyncio.create_task(publish_update())
        return PromptResponse(stop_reason=self.stop_reason, usage=self.usage)


class StructuredMetaACPAgent(EchoACPAgent):
    def __init__(self, *, structured_output: dict[str, Any]) -> None:
        super().__init__()
        self.structured_output = structured_output
        self.prompt_kwargs: list[dict[str, Any]] = []

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del message_id
        rendered_prompt = "".join(str(getattr(block, "text", "")) for block in prompt)
        self.prompts.append((session_id, rendered_prompt))
        self.prompt_kwargs.append(kwargs)
        return PromptResponse(
            field_meta=build_structured_output_response_meta(self.structured_output),
            stop_reason=self.stop_reason,
            usage=self.usage,
        )


class StructuredAnswer(BaseModel):
    answer: str


def test_acp_client_provider_is_plain_pydantic_ai_provider() -> None:
    acp_agent = EchoACPAgent()
    provider, model = _build_provider_and_model(acp_agent)

    assert isinstance(provider, Provider)
    assert provider.client is acp_agent
    assert provider.name == "acp"
    assert provider.base_url == "acp://local"
    assert model.provider is provider
    assert model.system == "acp"
    assert model.model_name == "zed-agent"
    assert model.base_url == "acp://local"


async def test_pydantic_ai_agent_can_use_acp_as_just_a_provider() -> None:
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)
    agent = Agent(model)

    result = await agent.run("Summarize the ACP bridge")

    assert "Summarize the ACP bridge" in result.output
    assert acp_agent.initialized_protocols == [PROTOCOL_VERSION]
    assert acp_agent.session_cwds == ["/workspace"]
    assert acp_agent.session_models == [("session-1", "zed-agent")]
    assert len(acp_agent.prompts) == 1
    assert "Summarize the ACP bridge" in acp_agent.prompts[0][1]


def test_pydantic_acp_requires_pydantic_ai_v2() -> None:
    package_pyproject = Path("packages/adapters/pydantic-acp/pyproject.toml")
    data: dict[str, Any] = tomllib.loads(package_pyproject.read_text())
    dependencies: list[str] = data["project"]["dependencies"]
    pydantic_ai_dependency: str = next(
        dependency for dependency in dependencies if dependency.startswith("pydantic-ai-slim")
    )

    assert ">=2.9.0" in pydantic_ai_dependency
    assert "<=2.16.0" in pydantic_ai_dependency
    assert "==1." not in pydantic_ai_dependency


# --- AcpProvider / AcpModel behavior (changed code) ---------------------------------


def test_acp_provider_accepts_acp_agent_directly_and_exposes_accessors() -> None:
    acp_agent = EchoACPAgent()

    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent))
    model = provider.model(history_mode="full")

    assert provider.client is acp_agent
    assert provider.host.delegate is None
    assert provider.name == "acp"
    assert provider.base_url == "acp://local"
    assert provider.session_id is None
    assert provider.updates == []
    assert model.provider is provider


def test_acp_provider_requires_acp_agent_keyword() -> None:
    acp_agent = EchoACPAgent()

    with pytest.raises(TypeError, match="required keyword-only argument: 'acp_agent'"):
        cast(Any, AcpProvider)(cwd="/workspace")

    with pytest.raises(TypeError, match="unexpected keyword argument 'agent'"):
        cast(Any, AcpProvider)(agent=acp_agent, cwd="/workspace")


async def test_acp_provider_reuses_session_and_model_across_multiple_requests() -> None:
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)
    agent = Agent(model)

    await agent.run("first turn")
    await agent.run("second turn")

    assert acp_agent.initialized_protocols == [PROTOCOL_VERSION]
    assert acp_agent.session_cwds == ["/workspace"]
    assert acp_agent.session_models == [("session-1", "zed-agent")]
    assert len(acp_agent.prompts) == 2


async def test_acp_provider_waits_for_prompt_session_update_notifications() -> None:
    acp_agent = DelayedUpdateACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)

    response = await model.request(
        [ModelRequest(parts=[UserPromptPart("late notification")])],
        None,
        ModelRequestParameters(),
    )

    assert len(response.parts) == 1
    assert isinstance(response.parts[0], TextPart)
    assert response.parts[0].content == "delayed acp echo: late notification"


def test_acp_provider_model_factory_uses_default_model_name() -> None:
    acp_agent = EchoACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent), cwd="/workspace")

    model = provider.model()

    assert model.model_name == "agent"
    assert model.provider is provider


def test_create_acp_model_requires_exactly_one_agent_source() -> None:
    acp_agent = cast(AcpAgent, EchoACPAgent())

    with pytest.raises(ValueError, match="Exactly one of acp_agent or acp_command"):
        create_acp_model()

    with pytest.raises(ValueError, match="Exactly one of acp_agent or acp_command"):
        create_acp_model(acp_agent=acp_agent, acp_command=(sys.executable, "-c", "pass"))

    with pytest.raises(ValueError, match="at least one executable argument"):
        create_acp_model(acp_command=())

    with pytest.raises(TypeError, match="not a string"):
        create_acp_model(acp_command=cast(Any, sys.executable))


async def test_create_acp_model_wraps_in_process_acp_agent() -> None:
    acp_agent = EchoACPAgent()
    model = create_acp_model(
        acp_agent=cast(AcpAgent, acp_agent),
        model_name="zed-agent",
        cwd="/workspace",
    )
    agent = Agent(model)

    result = await agent.run("factory path")

    assert "factory path" in result.output
    assert acp_agent.initialized_protocols == [PROTOCOL_VERSION]
    assert acp_agent.session_cwds == ["/workspace"]
    assert acp_agent.session_models == [("session-1", "zed-agent")]


async def test_create_acp_model_forwards_auth_and_empty_turn_options() -> None:
    auth_agent = AuthRequiredACPAgent(
        auth_methods=[AuthMethodAgent(id="login", name="Login")],
    )
    auth_model = create_acp_model(
        acp_agent=cast(AcpAgent, auth_agent),
        cwd="/workspace",
        auth_method_id="custom",
    )

    await cast(AcpProvider, auth_model.provider).ensure_session()

    assert auth_agent.authenticated_with == ["custom"]

    empty_model = create_acp_model(
        acp_agent=cast(AcpAgent, NoHandshakeACPAgent()),
        cwd="/workspace",
        raise_on_empty_turn=True,
    )
    with pytest.raises(UnexpectedModelBehavior, match="ACP agent ended its turn"):
        await empty_model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(),
        )


async def test_acp_provider_default_model_leaves_remote_model_selection_to_agent() -> None:
    acp_agent = EchoACPAgent()
    set_session_model = AsyncMock(
        side_effect=RequestError.invalid_params({"modelId": "agent"}),
    )
    cast(Any, acp_agent).set_session_model = set_session_model
    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent), cwd="/workspace")
    model = provider.model()

    result = await Agent(model).run("use the default ACP session model")

    assert "use the default ACP session model" in result.output
    assert model.model_name == "agent"
    assert acp_agent.session_cwds == ["/workspace"]
    assert acp_agent.session_models == []
    set_session_model.assert_not_awaited()


async def test_create_acp_model_command_runs_stdio_agent_without_model_selection(
    tmp_path: Path,
) -> None:
    server_script = tmp_path / "stdio_acp_agent.py"
    server_script.write_text(
        """
from __future__ import annotations

import asyncio
import os
from typing import Any

from acp import run_agent
from acp.helpers import text_block
from acp.schema import AgentCapabilities, AgentMessageChunk, Implementation, InitializeResponse, NewSessionResponse, PromptResponse


class StdioAgent:
    def __init__(self) -> None:
        self.client: Any | None = None

    def on_connect(self, conn: Any) -> None:
        self.client = conn

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        del kwargs
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(name="stdio-test-agent", version="test"),
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, cwd: str, **kwargs: Any) -> NewSessionResponse:
        del cwd, kwargs
        return NewSessionResponse(session_id="stdio-session")

    async def set_session_model(self, **kwargs: Any) -> None:
        raise AssertionError(f"set_session_model must not be called: {kwargs!r}")

    async def prompt(self, prompt: list[Any], session_id: str, **kwargs: Any) -> PromptResponse:
        del kwargs
        if self.client is None:
            raise AssertionError("agent was not connected")
        rendered = "".join(str(getattr(block, "text", "")) for block in prompt)
        payload = f"{os.environ['ACP_COMMAND_MARKER']}|{os.getcwd()}|{rendered}"
        await self.client.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=text_block(payload),
            ),
            source="stdio-test-agent",
        )
        return PromptResponse(stop_reason="end_turn")


asyncio.run(run_agent(StdioAgent()))
""",
    )
    model = create_acp_model(
        acp_command=(sys.executable, str(server_script)),
        cwd=tmp_path,
        env={"ACP_COMMAND_MARKER": "from-env"},
        stderr_mode="discard",
        terminate_timeout=1.0,
    )

    async with model:
        response = await model.request(
            [ModelRequest(parts=[UserPromptPart("stdio factory path")])],
            None,
            ModelRequestParameters(),
        )

    output_prefix = f"from-env|{tmp_path}|"
    assert len(response.parts) == 1
    assert isinstance(response.parts[0], TextPart)
    assert response.parts[0].content == f"{output_prefix}stdio factory path"
    command_agent = cast(Any, model.provider).client
    assert command_agent._process is None
    assert command_agent._connection is None


async def test_create_acp_model_command_supports_outer_agent_structured_output(
    tmp_path: Path,
) -> None:
    server_script = tmp_path / "stdio_structured_acp_agent.py"
    seen_meta_path = tmp_path / "seen_meta.json"
    server_script.write_text(
        """
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from acp import run_agent
from acp.schema import AgentCapabilities, Implementation, InitializeResponse, NewSessionResponse, PromptResponse


class StructuredStdioAgent:
    def __init__(self) -> None:
        self.client: Any | None = None

    def on_connect(self, conn: Any) -> None:
        self.client = conn

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        del kwargs
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(name="structured-stdio-agent", version="test"),
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, cwd: str, **kwargs: Any) -> NewSessionResponse:
        del cwd, kwargs
        return NewSessionResponse(session_id="structured-stdio-session")

    async def set_session_model(self, **kwargs: Any) -> None:
        raise AssertionError(f"set_session_model must not be called: {kwargs!r}")

    async def prompt(self, prompt: list[Any], session_id: str, **kwargs: Any) -> PromptResponse:
        del prompt, session_id
        request_meta = kwargs.get("_meta")
        Path("seen_meta.json").write_text(json.dumps(request_meta), encoding="utf-8")
        return PromptResponse(
            field_meta={
                "pydantic_acp": {
                    "version": 1,
                    "structured_output": {
                        "output": {"answer": "from stdio"},
                    },
                },
            },
            stop_reason="end_turn",
        )


asyncio.run(run_agent(StructuredStdioAgent()))
""",
    )
    model = create_acp_model(
        acp_command=(sys.executable, str(server_script)),
        cwd=tmp_path,
        stderr_mode="discard",
        terminate_timeout=1.0,
        enable_pydantic_acp_meta=True,
    )
    agent = Agent(model, output_type=StructuredAnswer)

    async with model:
        result = await agent.run("answer through stdio structured meta")

    assert result.output == StructuredAnswer(answer="from stdio")
    seen_meta = json.loads(seen_meta_path.read_text(encoding="utf-8"))
    structured_request = seen_meta["pydantic_acp"]["structured_output"]
    assert isinstance(structured_request["allow_text_output"], bool)
    assert structured_request["output_tools"][0]["parameters_json_schema"]["title"] == (
        "StructuredAnswer"
    )
    command_agent = cast(Any, model.provider).client
    assert command_agent._process is None
    assert command_agent._connection is None


def test_acp_command_options_validate_runtime_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one executable argument"):
        command_agent_module.AcpCommandOptions(command=(), cwd=tmp_path)

    with pytest.raises(ValueError, match="stderr_mode"):
        command_agent_module.AcpCommandOptions(
            command=("agent",),
            cwd=tmp_path,
            stderr_mode=cast(Any, "pipe"),
        )

    with pytest.raises(ValueError, match="positive finite"):
        command_agent_module.AcpCommandOptions(
            command=("agent",),
            cwd=tmp_path,
            terminate_timeout=0,
        )

    assert command_agent_module._build_process_env(None) is None
    merged = command_agent_module._build_process_env({"ACP_COMMAND_MARKER": "merged"})
    assert merged is not None
    assert merged["ACP_COMMAND_MARKER"] == "merged"


async def test_acp_provider_close_is_noop_for_agents_without_close() -> None:
    provider = AcpProvider(acp_agent=cast(AcpAgent, EchoACPAgent()))

    await provider.close()

    assert provider.client is not None


async def test_acp_provider_nested_context_closes_only_after_outer_exit() -> None:
    class ClosableACPAgent(EchoACPAgent):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    acp_agent = ClosableACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent))

    async with provider:
        async with provider:
            assert acp_agent.close_calls == 0
        assert acp_agent.close_calls == 0

    assert acp_agent.close_calls == 1


async def test_acp_provider_close_accepts_sync_close_hook() -> None:
    class SyncClosableACPAgent(EchoACPAgent):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    acp_agent = SyncClosableACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent))

    await provider.close()

    assert acp_agent.close_calls == 1


async def test_acp_command_agent_delegates_session_methods_through_existing_connection(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class LiveProcess:
        returncode = None

    class DelegatingConnection:
        async def load_session(self, **kwargs: Any) -> str:
            calls.append(("load_session", kwargs))
            return "load"

        async def list_sessions(self, **kwargs: Any) -> str:
            calls.append(("list_sessions", kwargs))
            return "list"

        async def set_session_mode(self, **kwargs: Any) -> str:
            calls.append(("set_session_mode", kwargs))
            return "mode"

        async def set_config_option(self, **kwargs: Any) -> str:
            calls.append(("set_config_option", kwargs))
            return "config"

        async def authenticate(self, **kwargs: Any) -> str:
            calls.append(("authenticate", kwargs))
            return "auth"

        async def fork_session(self, **kwargs: Any) -> str:
            calls.append(("fork_session", kwargs))
            return "fork"

        async def resume_session(self, **kwargs: Any) -> str:
            calls.append(("resume_session", kwargs))
            return "resume"

        async def close_session(self, **kwargs: Any) -> str:
            calls.append(("close_session", kwargs))
            return "close-session"

        async def cancel(self, **kwargs: Any) -> None:
            calls.append(("cancel", kwargs))

        async def ext_method(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("ext_method", kwargs))
            return {"ok": True}

        async def ext_notification(self, **kwargs: Any) -> None:
            calls.append(("ext_notification", kwargs))

    command_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(command=("agent",), cwd=tmp_path),
    )
    cast(Any, command_agent)._process = LiveProcess()
    cast(Any, command_agent)._connection = DelegatingConnection()

    assert await command_agent.load_session(cwd="/workspace", session_id="s1") == "load"
    assert await command_agent.list_sessions(cursor="c1", cwd="/workspace") == "list"
    assert await command_agent.set_session_mode(mode_id="review", session_id="s1") == "mode"
    assert await command_agent.set_session_model(model_id="m1", session_id="s1") == "config"
    assert (
        await command_agent.set_config_option(
            config_id="effort",
            session_id="s1",
            value="high",
        )
        == "config"
    )
    assert await command_agent.authenticate(method_id="oauth") == "auth"
    assert await command_agent.fork_session(cwd="/workspace", session_id="s1") == "fork"
    assert await command_agent.resume_session(cwd="/workspace", session_id="s1") == "resume"
    assert await command_agent.close_session(session_id="s1") == "close-session"
    await command_agent.cancel(session_id="s1")
    assert await command_agent.ext_method(method="x/test", params={"a": 1}) == {"ok": True}
    await command_agent.ext_notification(method="x/event", params={"b": 2})

    assert [name for name, _kwargs in calls] == [
        "load_session",
        "list_sessions",
        "set_session_mode",
        "set_config_option",
        "set_config_option",
        "authenticate",
        "fork_session",
        "resume_session",
        "close_session",
        "cancel",
        "ext_method",
        "ext_notification",
    ]


async def test_acp_command_agent_reuses_connection_created_while_waiting_for_lock(
    tmp_path: Path,
) -> None:
    class LiveProcess:
        returncode = None

    class MutatingLock:
        async def __aenter__(self) -> None:
            cast(Any, command_agent)._process = LiveProcess()
            cast(Any, command_agent)._connection = connection

        async def __aexit__(self, *args: Any) -> None:
            del args

    command_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(command=("agent",), cwd=tmp_path),
    )
    connection = object()
    cast(Any, command_agent)._get_connect_lock = lambda: MutatingLock()

    assert await cast(Any, command_agent)._ensure_connection() is connection


async def test_acp_command_agent_reuses_connect_lock_in_same_event_loop(
    tmp_path: Path,
) -> None:
    command_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(command=("agent",), cwd=tmp_path),
    )

    first_lock = cast(Any, command_agent)._get_connect_lock()
    second_lock = cast(Any, command_agent)._get_connect_lock()

    assert second_lock is first_lock


async def test_acp_command_agent_open_connection_errors_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(command=("agent",), cwd=tmp_path),
    )

    await command_agent.close()
    with pytest.raises(RuntimeError, match="closed"):
        await command_agent.initialize(protocol_version=PROTOCOL_VERSION)

    unconnected_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(command=("agent",), cwd=tmp_path),
    )
    with pytest.raises(RuntimeError, match="on_connect"):
        await cast(Any, unconnected_agent)._open_connection()

    terminations: list[tuple[Any, float]] = []

    async def fake_terminate(process: Any, *, timeout: float) -> None:
        terminations.append((process, timeout))

    class MissingPipeProcess:
        returncode = None
        stdin = None
        stdout = None

    async def create_missing_pipe_process(
        options: command_agent_module.AcpCommandOptions,
    ) -> MissingPipeProcess:
        del options
        return MissingPipeProcess()

    monkeypatch.setattr(command_agent_module, "_terminate_process", fake_terminate)
    monkeypatch.setattr(
        command_agent_module, "_create_command_process", create_missing_pipe_process
    )

    missing_pipe_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(
            command=("agent",),
            cwd=tmp_path,
            terminate_timeout=2.0,
        ),
    )
    missing_pipe_agent.on_connect(cast(AcpClient, object()))
    with pytest.raises(RuntimeError, match="stdio pipes"):
        await cast(Any, missing_pipe_agent)._open_connection()
    assert terminations[-1][1] == 2.0

    class PipeProcess:
        returncode = None
        stdin = object()
        stdout = object()

    pipe_process = PipeProcess()

    async def create_pipe_process(
        options: command_agent_module.AcpCommandOptions,
    ) -> PipeProcess:
        del options
        return pipe_process

    def raise_connect(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("connect failed")

    monkeypatch.setattr(command_agent_module, "_create_command_process", create_pipe_process)
    monkeypatch.setattr(command_agent_module, "connect_to_agent", raise_connect)

    failing_connect_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(command=("agent",), cwd=tmp_path),
    )
    failing_connect_agent.on_connect(cast(AcpClient, object()))
    with pytest.raises(RuntimeError, match="connect failed"):
        await cast(Any, failing_connect_agent)._open_connection()
    assert terminations[-1][0] is pipe_process


async def test_acp_command_agent_close_current_connection_accepts_sync_close(
    tmp_path: Path,
) -> None:
    class SyncCloseConnection:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    connection = SyncCloseConnection()
    command_agent = command_agent_module.AcpCommandAgent(
        options=command_agent_module.AcpCommandOptions(command=("agent",), cwd=tmp_path),
    )
    cast(Any, command_agent)._connection = connection

    await cast(Any, command_agent)._close_current_connection()

    assert connection.close_calls == 1
    assert cast(Any, command_agent)._connection is None


async def test_acp_command_agent_terminate_process_paths() -> None:
    class AlreadyExitedProcess:
        returncode = 0
        stdin = None

    await command_agent_module._terminate_process(cast(Any, AlreadyExitedProcess()), timeout=0.01)

    class ClosingStdin:
        def __init__(self, process: Any) -> None:
            self.process = process
            self.closed = False

        def close(self) -> None:
            self.closed = True
            self.process.returncode = 0

        async def wait_closed(self) -> None:
            return None

    class ExitsAfterStdinCloseProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.stdin = ClosingStdin(self)

    exits_after_stdin = ExitsAfterStdinCloseProcess()
    await command_agent_module._terminate_process(cast(Any, exits_after_stdin), timeout=0.01)
    assert exits_after_stdin.stdin.closed

    class LookupErrorProcess:
        returncode = None
        stdin = None

        def __init__(self) -> None:
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

        async def wait(self) -> None:
            raise ProcessLookupError

    lookup_error_process = LookupErrorProcess()
    await command_agent_module._terminate_process(cast(Any, lookup_error_process), timeout=0.01)
    assert lookup_error_process.terminate_calls == 1

    class ExitsAfterTimeoutProcess:
        returncode = None
        stdin = None

        def terminate(self) -> None:
            return None

        async def wait(self) -> None:
            self.returncode = 0
            raise TimeoutError

    exits_after_timeout = ExitsAfterTimeoutProcess()
    await command_agent_module._terminate_process(cast(Any, exits_after_timeout), timeout=0.01)
    assert exits_after_timeout.returncode == 0

    class TimeoutProcess:
        returncode = None
        stdin = None

        def __init__(self) -> None:
            self.terminate_calls = 0
            self.kill_calls = 0
            self.wait_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1

        async def wait(self) -> None:
            self.wait_calls += 1
            raise TimeoutError

    timeout_process = TimeoutProcess()
    await command_agent_module._terminate_process(cast(Any, timeout_process), timeout=0.01)
    assert timeout_process.terminate_calls == 1
    assert timeout_process.kill_calls == 1
    assert timeout_process.wait_calls == 2


async def test_acp_provider_model_history_mode_is_model_scoped() -> None:
    acp_agent = EchoACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent), cwd="/workspace")
    full_history_model = provider.model("model-a", history_mode="full")
    default_history_model = provider.model("model-b")
    messages = [
        ModelRequest(parts=[UserPromptPart("first turn")]),
        ModelResponse(parts=[TextPart("first answer")]),
        ModelRequest(parts=[UserPromptPart("second turn")]),
    ]

    await full_history_model.request(messages, None, ModelRequestParameters())
    await default_history_model.request(messages, None, ModelRequestParameters())

    assert "first answer" in acp_agent.prompts[0][1]
    assert acp_agent.prompts[1] == ("session-1", "second turn")


async def test_acp_provider_selects_model_through_config_option() -> None:
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)

    await Agent(model).run("sync model hook")

    assert acp_agent.session_models == [("session-1", "zed-agent")]


async def test_acp_provider_ensure_session_returns_existing_session_for_same_model() -> None:
    acp_agent = EchoACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent), cwd="/workspace")

    first_session_id = await provider._ensure_session(model_name="zed-agent")
    second_session_id = await provider._ensure_session(model_name="zed-agent")

    assert first_session_id == "session-1"
    assert second_session_id == "session-1"
    assert acp_agent.session_models == [("session-1", "zed-agent")]


async def test_acp_provider_uses_prompt_response_metadata_as_a_text_fallback() -> None:
    provider, _model = _build_provider_and_model(EchoACPAgent())

    text = await provider._agent_message_text_after_prompt(
        0,
        session_id="session-1",
        prompt_response=SimpleNamespace(response="metadata fallback"),
    )

    assert text == "metadata fallback"


async def test_acp_provider_requires_a_model_config_option_for_explicit_model_selection() -> None:
    class NoModelConfigACPAgent(EchoACPAgent):
        async def new_session(
            self,
            cwd: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> NewSessionResponse:
            response = await super().new_session(cwd, mcp_servers, **kwargs)
            return NewSessionResponse(session_id=response.session_id, config_options=[])

    _provider, model = _build_provider_and_model(NoModelConfigACPAgent())

    with pytest.raises(UserError, match="does not expose a selectable 'model'"):
        await Agent(model).run("hello")


async def test_acp_agent_test_doubles_preserve_legacy_model_and_connection_failures() -> None:
    acp_agent = EchoACPAgent()
    await acp_agent.set_session_model(model_id="legacy-model", session_id="session-1")
    assert acp_agent.session_models == [("session-1", "legacy-model")]

    with pytest.raises(AssertionError, match="not connected"):
        await DelayedUpdateACPAgent().prompt(prompt=[], session_id="session-1")


@pytest.mark.parametrize(
    "error",
    [
        RequestError(-32601, "Method not found", {"method": "session/set_model"}),
        RequestError(-32601, "session/set_model is unavailable"),
    ],
)
async def test_acp_provider_reraises_model_config_option_errors(
    error: RequestError,
) -> None:
    acp_agent = SetConfigOptionErrorACPAgent(error)
    _provider, model = _build_provider_and_model(acp_agent)

    with pytest.raises(RequestError):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(),
        )


async def test_acp_provider_custom_prompt_renderer_is_used_instead_of_default() -> None:
    acp_agent = EchoACPAgent()
    seen_message_counts: list[int] = []

    def render(messages: Any, params: Any) -> list[Any]:
        del params
        seen_message_counts.append(len(messages))
        return [text_block("rendered by custom renderer")]

    _provider, model = _build_provider_and_model(acp_agent, prompt_renderer=render)
    agent = Agent(model)

    result = await agent.run("ignored by custom renderer")

    assert seen_message_counts == [1]
    assert acp_agent.prompts == [("session-1", "rendered by custom renderer")]
    assert "acp echo: rendered by custom renderer" in result.output


async def test_acp_provider_custom_prompt_renderer_supports_async_callables() -> None:
    acp_agent = EchoACPAgent()

    async def render(messages: Any, params: Any) -> list[Any]:
        del messages, params
        return [text_block("rendered async")]

    _provider, model = _build_provider_and_model(acp_agent, prompt_renderer=render)
    agent = Agent(model)

    await agent.run("ignored")

    assert acp_agent.prompts == [("session-1", "rendered async")]


async def test_acp_provider_prefers_prompt_response_usage_over_host_updates() -> None:
    acp_agent = EchoACPAgent(
        usage=Usage(
            input_tokens=11,
            output_tokens=7,
            cached_read_tokens=2,
            cached_write_tokens=1,
            thought_tokens=3,
            total_tokens=24,
        ),
    )
    _provider, model = _build_provider_and_model(acp_agent)
    agent = Agent(model)

    result = await agent.run("count my tokens")

    usage = result.usage
    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.cache_read_tokens == 2
    assert usage.cache_write_tokens == 1
    assert usage.details.get("reasoning_tokens") == 3


@pytest.mark.parametrize(
    ("stop_reason", "expected_finish_reason"),
    [
        ("end_turn", "stop"),
        ("max_tokens", "length"),
        ("cancelled", "error"),
        ("refusal", "stop"),
        ("max_turn_requests", "stop"),
    ],
)
async def test_acp_provider_maps_acp_stop_reasons_to_finish_reasons(
    stop_reason: str,
    expected_finish_reason: str,
) -> None:
    acp_agent = EchoACPAgent(
        stop_reason=cast(
            Literal["end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"],
            stop_reason,
        )
    )
    _provider, model = _build_provider_and_model(acp_agent)

    response = await model.request(
        [ModelRequest(parts=[UserPromptPart("hello")])],
        None,
        ModelRequestParameters(),
    )

    assert response.finish_reason == expected_finish_reason
    assert response.provider_details == {"acp_session_id": "session-1"}


async def test_acp_model_rejects_function_tools() -> None:
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)

    with pytest.raises(UserError, match="function tools"):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(function_tools=[ToolDefinition(name="do_thing")]),
        )
    assert acp_agent.prompts == []


async def test_acp_model_rejects_native_tools() -> None:
    # AcpModel advertises no supported native tools, so pydantic-ai's own
    # `Model.prepare_request` rejects the native tool before AcpModel's own
    # `_ensure_supported_request` guard is ever reached.
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)

    with pytest.raises(UserError, match="(?i)native tool"):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(native_tools=[WebSearchTool()]),
        )
    assert acp_agent.prompts == []


def test_acp_model_own_guard_rejects_native_tools() -> None:
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)

    with pytest.raises(UserError, match="native tools directly"):
        model._ensure_supported_request(ModelRequestParameters(native_tools=[WebSearchTool()]))


async def test_acp_model_rejects_disallowed_text_output() -> None:
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)

    with pytest.raises(UserError, match="text-response provider bridge"):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(allow_text_output=False),
        )
    assert acp_agent.prompts == []


async def test_acp_model_can_round_trip_structured_output_over_private_meta() -> None:
    acp_agent = StructuredMetaACPAgent(structured_output={"answer": "42"})
    provider = AcpProvider(
        acp_agent=cast(AcpAgent, acp_agent),
        cwd="/workspace",
        enable_pydantic_acp_meta=True,
    )
    model = provider.model()
    output_tool = ToolDefinition(
        name="final_result",
        parameters_json_schema=StructuredAnswer.model_json_schema(),
    )

    response = await model.request(
        [ModelRequest(parts=[UserPromptPart("answer structurally")])],
        None,
        ModelRequestParameters(output_tools=[output_tool], allow_text_output=False),
    )

    request_meta = acp_agent.prompt_kwargs[0]["_meta"]
    structured_request = request_meta["pydantic_acp"]["structured_output"]
    assert structured_request["allow_text_output"] is False
    assert structured_request["output_tools"][0]["name"] == "final_result"
    assert len(response.parts) == 1
    assert isinstance(response.parts[0], ToolCallPart)
    assert response.parts[0].tool_name == "final_result"
    assert response.parts[0].args == {"answer": "42"}


async def test_acp_provider_private_meta_supports_outer_agent_structured_output() -> None:
    acp_agent = StructuredMetaACPAgent(structured_output={"answer": "42"})
    provider = AcpProvider(
        acp_agent=cast(AcpAgent, acp_agent),
        cwd="/workspace",
        enable_pydantic_acp_meta=True,
    )
    agent = Agent(provider.model(), output_type=StructuredAnswer)

    result = await agent.run("answer structurally")

    assert result.output == StructuredAnswer(answer="42")
    request_meta = acp_agent.prompt_kwargs[0]["_meta"]
    structured_request = request_meta["pydantic_acp"]["structured_output"]
    assert structured_request["output_tools"][0]["parameters_json_schema"]["title"] == (
        "StructuredAnswer"
    )


async def test_acp_provider_auto_enables_private_meta_for_pydantic_acp_agents() -> None:
    inner_model = TestModel(custom_output_args={"answer": "42"})
    acp_agent = create_acp_agent(agent=Agent(inner_model, output_type=StructuredAnswer))
    provider = AcpProvider(acp_agent=acp_agent, cwd="/workspace")
    outer_agent = Agent(provider.model(), output_type=StructuredAnswer)

    result = await outer_agent.run("answer structurally")

    assert provider.enable_pydantic_acp_meta is True
    assert result.output == StructuredAnswer(answer="42")


async def test_acp_provider_can_disable_auto_private_meta_for_pydantic_acp_agents() -> None:
    inner_model = TestModel(custom_output_args={"answer": "42"})
    acp_agent = create_acp_agent(agent=Agent(inner_model, output_type=StructuredAnswer))
    provider = AcpProvider(
        acp_agent=acp_agent,
        cwd="/workspace",
        enable_pydantic_acp_meta=False,
    )
    outer_agent = Agent(provider.model(), output_type=StructuredAnswer)

    with pytest.raises(UserError, match="Tool output is not supported"):
        await outer_agent.run("answer structurally")


async def test_acp_model_fails_closed_when_structured_meta_is_missing() -> None:
    acp_agent = EchoACPAgent()
    provider = AcpProvider(
        acp_agent=cast(AcpAgent, acp_agent),
        cwd="/workspace",
        enable_pydantic_acp_meta=True,
    )
    model = provider.model()
    output_tool = ToolDefinition(
        name="final_result",
        parameters_json_schema=StructuredAnswer.model_json_schema(),
    )

    with pytest.raises(UserError, match="did not return pydantic_acp structured output"):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("answer structurally")])],
            None,
            ModelRequestParameters(output_tools=[output_tool], allow_text_output=False),
        )


def test_acp_model_rejects_unsolicited_structured_output_meta() -> None:
    acp_agent = EchoACPAgent()
    provider = AcpProvider(
        acp_agent=cast(AcpAgent, acp_agent),
        cwd="/workspace",
        enable_pydantic_acp_meta=True,
    )
    model = AcpModel(model_name="agent", provider=provider)
    prompt_result = client_module._AcpPromptResult(
        text="",
        usage=RequestUsage(),
        stop_reason="end_turn",
        session_id="session-1",
        structured_output={"answer": "42"},
    )

    with pytest.raises(UserError, match="did not include an output tool"):
        model._response_parts(prompt_result, ModelRequestParameters())


def test_pydantic_acp_private_meta_helpers_ignore_missing_or_invalid_payloads() -> None:
    class AliasMeta:
        _meta = {"alias": True}

    assert build_structured_output_request_meta(ModelRequestParameters()) is None
    assert extract_field_meta({"_meta": {"ok": True}}) == {"ok": True}
    assert extract_field_meta({"field_meta": "bad"}) is None
    assert extract_field_meta(AliasMeta()) == {"alias": True}
    assert has_structured_output_request(None) is False
    assert has_structured_output_request({"pydantic_acp": "bad"}) is False
    assert has_structured_output_request({"pydantic_acp": {"version": 999}}) is False
    assert (
        build_structured_output_type(
            {"pydantic_acp": {"version": 1, "structured_output": "bad"}},
        )
        is None
    )
    assert (
        build_structured_output_type(
            {"pydantic_acp": {"version": 1, "structured_output": {}}},
        )
        is None
    )
    assert (
        build_structured_output_type(
            {
                "pydantic_acp": {
                    "version": 1,
                    "structured_output": {"output_tools": ["bad"]},
                },
            },
        )
        is None
    )
    assert (
        build_structured_output_type(
            {
                "pydantic_acp": {
                    "version": 1,
                    "structured_output": {
                        "output_tools": [{"parameters_json_schema": "bad"}],
                    },
                },
            },
        )
        is None
    )
    assert extract_structured_output({"pydantic_acp": {"version": 1}}) is (
        MISSING_STRUCTURED_OUTPUT
    )
    assert (
        extract_structured_output(
            {"pydantic_acp": {"version": 1, "structured_output": {}}},
        )
        is MISSING_STRUCTURED_OUTPUT
    )


async def test_acp_model_request_stream_yields_the_buffered_response_text() -> None:
    acp_agent = EchoACPAgent()
    _provider, model = _build_provider_and_model(acp_agent)

    async with model.request_stream(
        [ModelRequest(parts=[UserPromptPart("stream this")])],
        None,
        ModelRequestParameters(),
    ) as streamed_response:
        async for _ in streamed_response:
            pass
        final_response = streamed_response.get()

    assert len(final_response.parts) == 1
    assert isinstance(final_response.parts[0], TextPart)
    assert final_response.parts[0].content == "acp echo: stream this"
    assert streamed_response.finish_reason == "stop"


async def test_acp_buffered_stream_skips_non_text_response_parts() -> None:
    response = ModelResponse(
        parts=[ToolCallPart(tool_name="lookup", args={})],
        model_name="agent",
    )
    stream = client_module._AcpBufferedStreamedResponse(
        model_request_parameters=ModelRequestParameters(),
        response=response,
    )

    events = [event async for event in stream]
    await stream.close_stream()

    assert events == []
    assert stream.model_name == "agent"


async def test_acp_provider_switches_session_model_when_model_name_changes() -> None:
    acp_agent = EchoACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, acp_agent), cwd="/workspace")
    first_model = AcpModel(model_name="model-a", provider=provider)
    second_model = AcpModel(model_name="model-b", provider=provider)

    await Agent(first_model).run("first turn")
    await Agent(second_model).run("second turn")

    # The ACP session is created once and reused; only the model selection changes.
    assert acp_agent.session_cwds == ["/workspace"]
    assert acp_agent.session_models == [
        ("session-1", "model-a"),
        ("session-1", "model-b"),
    ]


async def test_acp_provider_forwards_client_capabilities_info_and_mcp_servers() -> None:
    acp_agent = EchoACPAgent()
    capabilities = ClientCapabilities()
    client_info = Implementation(name="custom-client", version="9.9.9")
    mcp_servers = [{"name": "demo"}]

    provider = AcpProvider(
        acp_agent=cast(AcpAgent, acp_agent),
        cwd="/workspace",
        client_capabilities=capabilities,
        client_info=client_info,
        mcp_servers=mcp_servers,
    )
    model = AcpModel(model_name="zed-agent", provider=provider)

    await Agent(model).run("hello")

    assert acp_agent.client_capabilities is capabilities
    assert acp_agent.client_info is client_info
    assert acp_agent.mcp_servers_seen == [mcp_servers]


def test_acp_provider_model_profile_returns_the_shared_acp_profile() -> None:
    provider = AcpProvider(acp_agent=cast(AcpAgent, EchoACPAgent()), cwd="/workspace")
    assert provider.model_profile("anything") is client_module.ACP_MODEL_PROFILE


def test_acp_model_supported_native_tools_is_empty() -> None:
    assert AcpModel.supported_native_tools() == frozenset()


class NoHandshakeACPAgent:  # type: ignore[misc]
    """An ACP agent that never receives a reverse connection via ``on_connect``.

    This models an ACP agent implementation that does not implement the
    optional ``on_connect`` hook. ``AcpProvider`` must still be constructible
    and usable; the agent simply never observes host updates, so the resulting
    model response carries no text.
    """

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info, kwargs
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(name="no-handshake-agent", version="test"),
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del cwd, mcp_servers, kwargs
        return NewSessionResponse(session_id="session-1")

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del prompt, session_id, message_id, kwargs
        return PromptResponse(stop_reason="end_turn")  # type: ignore[arg-type]


async def test_acp_provider_does_not_require_the_agent_to_support_on_connect() -> None:
    acp_agent = NoHandshakeACPAgent()
    provider = AcpProvider(acp_agent=acp_agent, cwd="/workspace")  # type: ignore
    model = provider.model()

    response = await model.request(
        [ModelRequest(parts=[UserPromptPart("hello")])],
        None,
        ModelRequestParameters(),
    )

    assert response.parts == []
    assert response.finish_reason == "stop"
    assert response.provider_details == {"acp_session_id": "session-1"}


async def test_echo_acp_agent_requires_a_connected_host_client() -> None:
    acp_agent = EchoACPAgent()

    with pytest.raises(AssertionError, match="connected to a host client"):
        await acp_agent.prompt(prompt=[text_block("hello")], session_id="session-1")


# --- AcpHostBridge behavior (changed code) -------------------------------------------


async def test_host_bridge_records_updates_without_a_delegate() -> None:
    bridge = AcpHostBridge()

    await bridge.session_update(
        session_id="session-1",
        update=AgentMessageChunk(session_update="agent_message_chunk", content=text_block("hi")),
    )

    assert len(bridge.updates) == 1
    assert bridge.updates[0].session_id == "session-1"
    assert bridge.updates[0].source is None
    assert bridge.agent_message_text_since(0, session_id="session-1") == "hi"


async def test_host_bridge_ignores_agent_message_chunks_without_text_content() -> None:
    class FakeChunk:
        session_update = "agent_message_chunk"
        content = object()

    bridge = AcpHostBridge()

    await bridge.session_update(
        session_id="session-1",
        update=FakeChunk(),
    )

    assert bridge.agent_message_text_since(0, session_id="session-1") == ""


async def test_host_bridge_forwards_session_update_to_delegate_when_present() -> None:
    delegate = RecordingClient()
    bridge = AcpHostBridge(delegate=delegate)
    update = AgentMessageChunk(session_update="agent_message_chunk", content=text_block("hi"))

    await bridge.session_update(session_id="session-1", update=update)

    assert bridge.updates[0].update is update
    assert delegate.updates == [("session-1", update)]


@pytest.mark.parametrize(
    "call",
    [
        lambda bridge: bridge.request_permission(
            options=[PermissionOption(kind="allow_once", name="Allow", option_id="allow")],
            session_id="session-1",
            tool_call=ToolCallUpdate(tool_call_id="call-1"),
        ),
        lambda bridge: bridge.write_text_file(
            content="data", path="/tmp/f", session_id="session-1"
        ),
        lambda bridge: bridge.read_text_file(path="/tmp/f", session_id="session-1"),
        lambda bridge: bridge.create_terminal(command="ls", session_id="session-1"),
        lambda bridge: bridge.ext_method(method="custom/thing", params={}),
    ],
)
async def test_host_bridge_raises_without_a_delegate_for_host_methods(
    call: Any,
) -> None:
    bridge = AcpHostBridge()

    with pytest.raises(RuntimeError, match="no host client delegate"):
        await call(bridge)


async def test_host_bridge_ext_notification_is_a_noop_without_a_delegate() -> None:
    bridge = AcpHostBridge()

    await bridge.ext_notification(method="custom/notify", params={})


async def test_host_bridge_supports_sync_delegate_methods() -> None:
    class SyncExtensionClient:
        def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            return {"method": method, "params": params}

    bridge = AcpHostBridge(delegate=cast(AcpClient, SyncExtensionClient()))

    result = await bridge.ext_method(method="custom/sync", params={"x": 1})
    bridge.on_connect(cast(AcpAgent, object()))

    assert result == {"method": "custom/sync", "params": {"x": 1}}


async def test_host_bridge_delegates_filesystem_and_terminal_calls_to_host_client() -> None:
    delegate = HostRecordingClient()
    bridge = AcpHostBridge(delegate=delegate)

    write_response = await bridge.write_text_file(
        content="hello", path="/tmp/f", session_id="session-1"
    )
    read_response = await bridge.read_text_file(path="/tmp/f", session_id="session-1")
    terminal_response = await bridge.create_terminal(command="ls", session_id="session-1")

    assert write_response is delegate.write_response
    assert read_response.content == "file:/tmp/f:None:None"
    assert terminal_response.terminal_id == "terminal-1"
    assert delegate.write_calls == [("session-1", "/tmp/f", "hello")]
    assert delegate.read_calls == [("session-1", "/tmp/f", None, None)]


async def test_host_bridge_delegates_request_permission_to_host_client() -> None:
    delegate = RecordingClient()
    delegate.queue_permission_selected("allow")
    bridge = AcpHostBridge(delegate=delegate)

    response = await bridge.request_permission(
        options=[PermissionOption(kind="allow_once", name="Allow", option_id="allow")],
        session_id="session-1",
        tool_call=ToolCallUpdate(tool_call_id="call-1"),
    )

    assert isinstance(response.outcome, AllowedOutcome)
    assert response.outcome.option_id == "allow"


async def test_host_bridge_delegates_elicitation_lifecycle_to_host_client() -> None:
    class ElicitationRecordingClient(RecordingClient):
        def __init__(self) -> None:
            super().__init__()
            self.create_calls: list[tuple[str, ElicitationMode]] = []
            self.completed_ids: list[str] = []

        async def create_elicitation(
            self,
            message: str,
            mode: ElicitationMode,
            **kwargs: Any,
        ) -> AcceptElicitationResponse:
            del kwargs
            self.create_calls.append((message, mode))
            return AcceptElicitationResponse(action="accept", content={"confirmed": True})

        async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
            del kwargs
            self.completed_ids.append(elicitation_id)

    delegate = ElicitationRecordingClient()
    bridge = AcpHostBridge(delegate=cast(AcpClient, delegate))
    mode = ElicitationFormSessionMode(
        session_id="session-1",
        requested_schema=ElicitationSchema(),
    )

    response = await bridge.create_elicitation(message="Confirm", mode=mode)
    await bridge.complete_elicitation(elicitation_id="elicitation-1")

    assert response.action == "accept"
    assert delegate.create_calls == [("Confirm", mode)]
    assert delegate.completed_ids == ["elicitation-1"]


async def test_host_bridge_on_connect_forwards_to_delegate_when_supported() -> None:  # type: ignore[misc]
    delegate = RecordingClient()
    bridge = AcpHostBridge(delegate=delegate)
    connected: list[Any] = []

    def on_connect_handler(conn: Any) -> None:
        connected.append(conn)

    cast(Any, delegate).on_connect = on_connect_handler

    sentinel_agent = cast(AcpAgent, object())
    bridge.on_connect(sentinel_agent)

    assert connected == [sentinel_agent]


async def test_host_bridge_delegates_terminal_lifecycle_calls_to_host_client() -> None:
    delegate = HostRecordingClient()
    bridge = AcpHostBridge(delegate=delegate)

    output = await bridge.terminal_output(session_id="session-1", terminal_id="terminal-1")
    release = await bridge.release_terminal(session_id="session-1", terminal_id="terminal-1")
    wait = await bridge.wait_for_terminal_exit(session_id="session-1", terminal_id="terminal-1")
    kill = await bridge.kill_terminal(session_id="session-1", terminal_id="terminal-1")

    assert output.output == "terminal-output"
    assert release is delegate.release_response
    assert wait.exit_code == 0
    assert kill is delegate.kill_response
    assert delegate.output_calls == [("session-1", "terminal-1")]
    assert delegate.release_calls == [("session-1", "terminal-1")]
    assert delegate.wait_calls == [("session-1", "terminal-1")]
    assert delegate.kill_calls == [("session-1", "terminal-1")]


class ExtensionRecordingClient(RecordingClient):
    """A host client double that actually answers ACP extension calls."""

    def __init__(self) -> None:
        super().__init__()
        self.ext_method_calls: list[tuple[str, dict[str, Any]]] = []
        self.ext_notification_calls: list[tuple[str, dict[str, Any]]] = []

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ext_method_calls.append((method, params))
        return {"ok": True}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.ext_notification_calls.append((method, params))


async def test_host_bridge_delegates_extension_calls_to_host_client() -> None:
    delegate = ExtensionRecordingClient()
    bridge = AcpHostBridge(delegate=delegate)

    result = await bridge.ext_method(method="custom/thing", params={"x": 1})
    await bridge.ext_notification(method="custom/notify", params={"y": 2})

    assert result == {"ok": True}
    assert delegate.ext_method_calls == [("custom/thing", {"x": 1})]
    assert delegate.ext_notification_calls == [("custom/notify", {"y": 2})]


# --- Pure helper functions (changed code) --------------------------------------------


def test_usage_from_acp_extracts_token_counts_and_reasoning_tokens() -> None:
    usage = client_module._usage_from_acp(
        Usage(
            input_tokens=10,
            output_tokens=5,
            cached_read_tokens=2,
            cached_write_tokens=1,
            thought_tokens=4,
            total_tokens=18,
        ),
    )

    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 2
    assert usage.cache_write_tokens == 1
    assert usage.details == {"reasoning_tokens": 4}


def test_usage_from_acp_returns_empty_usage_for_none() -> None:
    usage = client_module._usage_from_acp(None)

    assert not usage.has_values()


def test_default_render_prompt_blocks_covers_system_tool_and_retry_parts() -> None:
    request = ModelRequest(
        parts=[
            SystemPromptPart("Be terse."),
            UserPromptPart("What is the status?"),
            ToolReturnPart(tool_name="check_status", content="ok", tool_call_id="call-1"),
            RetryPromptPart(
                content="please retry", tool_name="check_status", tool_call_id="call-1"
            ),
        ],
    )

    blocks = client_module._default_render_prompt_blocks([request], ModelRequestParameters())

    assert len(blocks) == 1
    rendered = blocks[0].text
    assert "System:\nBe terse." in rendered
    assert "What is the status?" in rendered
    assert "Tool result: check_status" in rendered
    assert "please retry" in rendered


def test_default_render_prompt_blocks_covers_instructions_and_full_history_roles() -> None:
    request = ModelRequest(
        parts=[UserPromptPart("What changed?")],
        instructions="Answer as release notes.",
    )
    response = ModelResponse(parts=[TextPart("The bridge was added.")])

    blocks = client_module._default_render_prompt_blocks(
        [request, response],
        ModelRequestParameters(),
        history_mode="full",
    )

    assert len(blocks) == 1
    rendered = blocks[0].text
    assert "<user>" in rendered
    assert "Instructions:\nAnswer as release notes." in rendered
    assert "<assistant>" in rendered
    assert "The bridge was added." in rendered


def test_default_render_prompt_blocks_covers_media_user_content() -> None:
    request = ModelRequest(
        parts=[
            UserPromptPart(
                content=[
                    "Look at this:",
                    TextContent("typed text"),
                    ImageUrl(url="https://example.com/x.png"),
                    BinaryContent(data=b"abc", media_type="text/plain"),
                    UploadedFile(file_id="file-1", provider_name="openai"),
                ],
            ),
        ],
    )

    blocks = client_module._default_render_prompt_blocks([request], ModelRequestParameters())

    rendered = blocks[0].text
    assert "Look at this:" in rendered
    assert "typed text" in rendered
    assert "[image-url:https://example.com/x.png]" in rendered
    assert "[binary:text/plain:3 bytes]" in rendered
    assert "[uploaded-file:openai:file-1]" in rendered


def test_default_render_prompt_blocks_falls_back_to_message_text_without_a_request() -> None:
    # No `ModelRequest` is present, so rendering must fall back to `message.text`
    # instead of the request-part renderer.
    response = ModelResponse(parts=[TextPart("previous turn output")])

    blocks = client_module._default_render_prompt_blocks([response], ModelRequestParameters())

    assert len(blocks) == 1
    assert blocks[0].text == "previous turn output"


def test_default_render_prompt_blocks_returns_nothing_for_empty_messages() -> None:
    assert client_module._default_render_prompt_blocks([], ModelRequestParameters()) == []


def test_default_render_prompt_blocks_returns_nothing_for_blank_latest_message() -> None:
    response = ModelResponse(parts=[TextPart("   ")])

    blocks = client_module._default_render_prompt_blocks([response], ModelRequestParameters())

    assert len(blocks) == 1
    assert blocks[0].text == "<assistant>\n   \nassistant>"


def test_default_render_prompt_blocks_ignores_unrendered_request_parts() -> None:
    request = ModelRequest(parts=cast(Any, [InstructionPart("internal instruction")]))

    assert client_module._default_render_prompt_blocks([request], ModelRequestParameters()) == []


def test_default_render_prompt_blocks_returns_nothing_for_blank_full_history() -> None:
    response = ModelResponse(parts=[])

    assert (
        client_module._default_render_prompt_blocks(
            [response],
            ModelRequestParameters(),
            history_mode="full",
        )
        == []
    )


def test_render_user_content_item_falls_back_to_runtime_kind() -> None:
    class UnknownContent:
        kind = "custom-content"

    assert (
        client_module._render_user_content_item(cast(Any, UnknownContent())) == "[custom-content]"
    )


def test_section_returns_empty_text_for_blank_content() -> None:
    assert client_module._section("Empty", " \n ") == ""


def test_is_agent_message_chunk_supports_duck_typed_updates() -> None:
    class FakeChunkLike:
        session_update = "agent_message_chunk"

    assert client_module._is_agent_message_chunk(FakeChunkLike()) is True
    assert client_module._is_agent_message_chunk(object()) is False


def test_extract_text_recursively_collects_supported_response_shapes() -> None:
    class AttrResponse:
        response = "attr text"

    chunk = AgentMessageChunk(
        session_update="agent_message_chunk",
        content=text_block("chunk text"),
    )

    extracted = client_module._extract_text(
        [
            None,
            "literal text",
            chunk,
            {"content": ["nested text", {"text": "dict text"}]},
            AttrResponse(),
        ],
    )

    assert extracted == "literal textchunk textnested textdict textattr text"


def test_extract_text_does_not_duplicate_content_field_strings() -> None:
    assert client_module._extract_text({"content": "content text"}) == "content text"


def test_response_field_supports_dict_attr_and_missing_values() -> None:
    class AttrResponse:
        message = "attr message"

    assert client_module._response_field({"message": "dict message"}, "message") == "dict message"
    assert client_module._response_field(AttrResponse(), "message") == "attr message"
    assert client_module._response_field(object(), "message") is None


# --- Prior (pre-existing) server adapter behavior path --------------------------------


async def test_prior_server_adapter_direction_still_works_standalone() -> None:
    """Regression guard: the original ACP server adapter direction is unaffected.

    This PR adds the inverse client/provider bridge, but the original
    ``create_acp_agent`` direction (exposing a ``pydantic_ai.Agent`` over ACP)
    must keep working exactly as it did before this change.
    """
    adapter = create_acp_agent(agent=Agent(TestModel(custom_output_text="Hello from ACP")))
    client = RecordingClient()
    adapter.on_connect(client)

    await adapter.initialize(protocol_version=PROTOCOL_VERSION)
    new_session_response = await adapter.new_session(cwd="/workspace", mcp_servers=[])
    prompt_response = await adapter.prompt(
        prompt=[text_block("Summarize the change.")],
        session_id=new_session_response.session_id,
        message_id="user-message-1",
    )

    assert prompt_response.stop_reason == "end_turn"
    agent_updates = [
        update for _, update in client.updates if isinstance(update, AgentMessageChunk)
    ]
    assert "".join(chunk.content.text for chunk in agent_updates) == "Hello from ACP"


async def test_server_adapter_returns_structured_output_private_meta_when_requested() -> None:
    inner_model = TestModel(custom_output_args={"answer": "42"})
    adapter = create_acp_agent(agent=Agent(inner_model, output_type=StructuredAnswer))
    client = RecordingClient()
    adapter.on_connect(client)

    await adapter.initialize(protocol_version=PROTOCOL_VERSION)
    new_session_response = await adapter.new_session(cwd="/workspace", mcp_servers=[])
    output_tool = ToolDefinition(
        name="final_result",
        parameters_json_schema=StructuredAnswer.model_json_schema(),
    )
    request_meta = build_structured_output_request_meta(
        ModelRequestParameters(output_tools=[output_tool], allow_text_output=False),
    )

    prompt_response = await adapter.prompt(
        prompt=[text_block("Return structured data.")],
        session_id=new_session_response.session_id,
        message_id="user-message-1",
        _meta=request_meta,
    )

    response_meta = extract_field_meta(prompt_response)
    assert extract_structured_output(response_meta) == {"answer": "42"}


async def test_prior_server_adapter_can_be_consumed_through_new_client_provider() -> None:
    """The new AcpProvider/AcpModel bridge composes with the pre-existing server adapter.

    A Pydantic AI agent is exposed via the original ``create_acp_agent`` adapter
    (prior, unchanged behavior) and then that very adapter is wrapped by the new
    ``AcpProvider`` (this PR's change) so a *second* Pydantic AI agent can run
    against it as a plain provider/model. This proves both directions interoperate.
    """
    inner_model = TestModel(custom_output_text="Hello from ACP")
    server_adapter = create_acp_agent(agent=Agent(inner_model))

    _provider, model = _build_provider_and_model(server_adapter, model_name=inner_model.model_name)
    outer_agent = Agent(model)

    result = await outer_agent.run("Ping the bridge")

    assert result.output == "Hello from ACP"


# --- Additional coverage: AcpHostBridge pagination/session scoping ------------------


async def test_host_bridge_records_since_scopes_by_session_and_supports_snapshots() -> None:
    bridge = AcpHostBridge()
    first_update = AgentMessageChunk(session_update="agent_message_chunk", content=text_block("a"))
    second_update = AgentMessageChunk(session_update="agent_message_chunk", content=text_block("b"))

    await bridge.session_update(session_id="session-1", update=first_update)
    snapshot = bridge.snapshot_index()
    await bridge.session_update(session_id="session-2", update=second_update)

    all_records = bridge.records_since(0)
    assert [record.update for record in all_records] == [first_update, second_update]

    only_new = bridge.records_since(snapshot)
    assert [record.update for record in only_new] == [second_update]

    only_session_1 = bridge.records_since(0, session_id="session-1")
    assert [record.update for record in only_session_1] == [first_update]

    only_session_2_after_snapshot = bridge.records_since(snapshot, session_id="session-2")
    assert [record.update for record in only_session_2_after_snapshot] == [second_update]


async def test_host_bridge_usage_update_since_ignores_real_acp_usage_update_without_usage_field() -> (
    None
):
    # The real ACP `UsageUpdate` schema carries context-window `size`/`used` fields, not a
    # `usage` attribute. `usage_update_since` only reads `getattr(update, "usage", None)`, so
    # recording a genuine `UsageUpdate` must not populate any token counts.
    bridge = AcpHostBridge()
    start_index = bridge.snapshot_index()

    await bridge.session_update(
        session_id="session-1",
        update=UsageUpdate(session_update="usage_update", size=1000, used=10),
    )

    usage = bridge.usage_update_since(start_index, session_id="session-1")

    assert not usage.has_values()


# --- Additional coverage: AcpProvider host_client delegate wiring -------------------


async def test_acp_provider_forwards_host_client_delegate_updates_end_to_end() -> None:
    delegate = HostRecordingClient()
    acp_agent = EchoACPAgent()
    provider = AcpProvider(
        acp_agent=cast(AcpAgent, acp_agent),
        cwd="/workspace",
        host_client=delegate,
    )

    assert provider.host.delegate is delegate

    model = AcpModel(model_name="zed-agent", provider=provider)
    result = await Agent(model).run("hello via delegate")

    assert "acp echo: hello via delegate" in result.output
    forwarded_updates = [
        update for _, update in delegate.updates if isinstance(update, AgentMessageChunk)
    ]
    assert len(forwarded_updates) == 1
    assert forwarded_updates[0].content.text == "acp echo: hello via delegate"


# --- Additional coverage: pure helper edge cases -------------------------------------


def test_finish_reason_from_acp_returns_stop_when_stop_reason_is_none() -> None:
    assert client_module._finish_reason_from_acp(None) == "stop"


def test_render_tool_return_falls_back_to_tool_call_id_when_tool_name_is_empty() -> None:
    part = ToolReturnPart(tool_name="", content="ok", tool_call_id="call-42")

    rendered = client_module._render_tool_return(part)

    assert rendered == "Tool result: call-42:\nok"


# --- Additional coverage: root workspace pyproject.toml dependency -----------------


def test_root_pyproject_declares_pydantic_ai_v2_dependency() -> None:
    root_pyproject = Path("pyproject.toml")
    data: dict[str, Any] = tomllib.loads(root_pyproject.read_text())
    dependencies: list[str] = data["project"]["dependencies"]

    assert any(dependency.startswith("pydantic-ai>=2.9.0,<=2.16.0") for dependency in dependencies)


def test_pydantic_acp_pins_agent_client_protocol_version_used_by_client_module() -> None:
    package_pyproject = Path("packages/adapters/pydantic-acp/pyproject.toml")
    data: dict[str, Any] = tomllib.loads(package_pyproject.read_text())
    dependencies: list[str] = data["project"]["dependencies"]

    assert "agent-client-protocol==0.11.0" in dependencies


# --- Additional coverage: public package exports for the client bridge (__init__.py) -----


def test_acp_client_provider_symbols_are_exported_from_the_top_level_package() -> None:
    for name in (
        "ACP_MODEL_PROFILE",
        "AcpHostBridge",
        "AcpModel",
        "AcpPromptRenderer",
        "AcpProvider",
        "AcpUpdateRecord",
    ):
        assert name in pydantic_acp.__all__
        assert getattr(pydantic_acp, name) is getattr(client_module, name)


def test_acp_model_profile_disables_tool_and_structured_output_support() -> None:
    profile = pydantic_acp.ACP_MODEL_PROFILE

    assert profile["supports_tools"] is False
    assert profile["supports_json_schema_output"] is False
    assert profile["supports_json_object_output"] is False
    assert profile["supports_image_output"] is False
    assert profile["supported_native_tools"] == frozenset()


# ---------------------------------------------------------------------------
# Authentication recovery, explicit session bootstrap, and empty-turn handling
# ---------------------------------------------------------------------------


class AuthRequiredACPAgent(EchoACPAgent):
    """An ACP agent that rejects ``session/new`` until ``authenticate`` runs."""

    def __init__(self, *, auth_methods: list[TestAuthMethod], fail_times: int = 1) -> None:
        super().__init__()
        self._auth_methods = auth_methods
        self._fail_times = fail_times
        self.authenticated_with: list[str] = []
        self.new_session_calls = 0

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info, kwargs
        self.initialized_protocols.append(protocol_version)
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(name="auth-acp-agent", version="test"),
            agent_capabilities=AgentCapabilities(),
            auth_methods=self._auth_methods,
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        self.new_session_calls += 1
        if self.new_session_calls <= self._fail_times:
            raise RequestError.auth_required()
        return await super().new_session(cwd, mcp_servers, **kwargs)

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        del kwargs
        self.authenticated_with.append(method_id)


async def test_acp_provider_authenticates_and_retries_session_new_on_auth_required() -> None:
    agent = AuthRequiredACPAgent(
        auth_methods=[AuthMethodAgent(id="login", name="Login", description="Log in")],
    )
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    session_id = await provider.ensure_session()

    assert session_id == "session-1"
    assert agent.new_session_calls == 2
    assert agent.authenticated_with == ["login"]


async def test_acp_provider_uses_explicit_auth_method_id_when_configured() -> None:
    agent = AuthRequiredACPAgent(
        auth_methods=[AuthMethodAgent(id="login", name="Login", description="Log in")],
    )
    provider = AcpProvider(
        acp_agent=cast(AcpAgent, agent), cwd="/workspace", auth_method_id="custom"
    )

    await provider.ensure_session()

    assert agent.authenticated_with == ["custom"]


async def test_acp_provider_auth_required_without_any_method_raises_userror() -> None:
    agent = AuthRequiredACPAgent(auth_methods=[])
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    with pytest.raises(UserError, match="authentication"):
        await provider.ensure_session()


async def test_acp_provider_auth_required_without_authenticate_support_raises_userror() -> None:
    class NoAuthenticateACPAgent(EchoACPAgent):
        async def new_session(
            self,
            cwd: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> NewSessionResponse:
            del cwd, mcp_servers, kwargs
            raise RequestError.auth_required()

    provider = AcpProvider(acp_agent=cast(AcpAgent, NoAuthenticateACPAgent()), cwd="/workspace")

    with pytest.raises(UserError, match="does not expose an 'authenticate' method"):
        await provider.ensure_session()


async def test_acp_provider_ensure_session_bootstraps_without_a_prompt() -> None:
    agent = EchoACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    session_id = await provider.ensure_session()

    assert session_id == "session-1"
    assert provider.session_id == "session-1"
    assert agent.initialized_protocols == [PROTOCOL_VERSION]
    assert agent.prompts == []


async def test_acp_provider_set_session_mode_delegates_to_agent() -> None:
    mode_calls: list[tuple[str, str]] = []

    class ModeACPAgent(EchoACPAgent):
        async def set_session_mode(
            self,
            session_id: str,
            mode_id: str,
            **kwargs: Any,
        ) -> None:
            del kwargs
            mode_calls.append((session_id, mode_id))

    provider = AcpProvider(acp_agent=cast(AcpAgent, ModeACPAgent()), cwd="/workspace")

    await provider.set_session_mode("default")

    assert mode_calls == [("session-1", "default")]


async def test_acp_provider_set_session_mode_without_support_raises_userror() -> None:
    provider = AcpProvider(acp_agent=cast(AcpAgent, EchoACPAgent()), cwd="/workspace")

    with pytest.raises(UserError, match="set_session_mode"):
        await provider.set_session_mode("default")


async def test_empty_turn_returns_empty_parts_by_default() -> None:
    provider = AcpProvider(acp_agent=cast(AcpAgent, NoHandshakeACPAgent()), cwd="/workspace")
    model = provider.model()

    response = await model.request(
        [ModelRequest(parts=[UserPromptPart("hello")])],
        None,
        ModelRequestParameters(),
    )

    assert response.parts == []


async def test_empty_turn_raises_acp_specific_error_when_opted_in() -> None:
    provider = AcpProvider(
        acp_agent=cast(AcpAgent, NoHandshakeACPAgent()),
        cwd="/workspace",
        raise_on_empty_turn=True,
    )
    model = provider.model()

    with pytest.raises(UnexpectedModelBehavior, match="ACP agent ended its turn"):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(),
        )


# ---------------------------------------------------------------------------
# Additional coverage: auth edge cases, session bootstrap, and empty turns
# ---------------------------------------------------------------------------


async def test_acp_provider_does_not_retry_authenticate_after_it_already_ran() -> None:
    """A second ``auth_required`` after authenticating must propagate, not loop."""
    agent = AuthRequiredACPAgent(
        auth_methods=[AuthMethodAgent(id="login", name="Login", description="Log in")],
        fail_times=2,
    )
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    with pytest.raises(RequestError) as exc_info:
        await provider.ensure_session()

    assert exc_info.value.code == RequestError.auth_required().code
    assert agent.new_session_calls == 2
    assert agent.authenticated_with == ["login"]


async def test_acp_provider_non_auth_request_error_propagates_without_authenticating() -> None:
    class OtherErrorACPAgent(EchoACPAgent):
        async def new_session(
            self,
            cwd: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> NewSessionResponse:
            del cwd, mcp_servers, kwargs
            raise RequestError.internal_error()

    provider = AcpProvider(acp_agent=cast(AcpAgent, OtherErrorACPAgent()), cwd="/workspace")

    with pytest.raises(RequestError) as exc_info:
        await provider.ensure_session()

    assert exc_info.value.code == RequestError.internal_error().code


async def test_acp_provider_uses_first_advertised_auth_method_when_multiple_exist() -> None:
    agent = AuthRequiredACPAgent(
        auth_methods=[
            AuthMethodAgent(id="oauth", name="OAuth", description="OAuth login"),
            AuthMethodAgent(id="apikey", name="API Key", description="API key login"),
        ],
    )
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    await provider.ensure_session()

    assert agent.authenticated_with == ["oauth"]


async def test_acp_provider_automatically_uses_only_agent_managed_auth_methods() -> None:
    agent = AuthRequiredACPAgent(
        auth_methods=[
            EnvVarAuthMethod(
                id="api-key",
                name="API key",
                type="env_var",
                vars=[AuthEnvVar(name="API_KEY")],
            ),
            AuthMethodAgent(id="oauth", name="OAuth", description="Browser login"),
        ],
    )
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    await provider.ensure_session()

    assert agent.authenticated_with == ["oauth"]


async def test_acp_provider_supports_synchronous_authenticate() -> None:
    # A *synchronous* authenticate() exercises the non-awaitable branch of
    # AcpProvider._authenticate. It subclasses EchoACPAgent (which has no
    # authenticate) rather than AuthRequiredACPAgent, so the sync method is a
    # fresh definition, not an invalid override of an async one.
    class SyncAuthenticateACPAgent(EchoACPAgent):
        def __init__(self) -> None:
            super().__init__()
            self.authenticated_with: list[str] = []
            self.new_session_calls = 0

        async def initialize(
            self,
            protocol_version: int,
            client_capabilities: ClientCapabilities | None = None,
            client_info: Implementation | None = None,
            **kwargs: Any,
        ) -> InitializeResponse:
            del client_capabilities, client_info, kwargs
            self.initialized_protocols.append(protocol_version)
            return InitializeResponse(
                protocol_version=protocol_version,
                agent_info=Implementation(name="sync-auth-agent", version="test"),
                agent_capabilities=AgentCapabilities(),
                auth_methods=[AuthMethodAgent(id="login", name="Login", description="Log in")],
            )

        async def new_session(
            self,
            cwd: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> NewSessionResponse:
            self.new_session_calls += 1
            if self.new_session_calls == 1:
                raise RequestError.auth_required()
            return await super().new_session(cwd, mcp_servers, **kwargs)

        def authenticate(self, method_id: str, **kwargs: Any) -> None:
            del kwargs
            self.authenticated_with.append(method_id)

    agent = SyncAuthenticateACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    session_id = await provider.ensure_session()

    assert session_id == "session-1"
    assert agent.new_session_calls == 2
    assert agent.authenticated_with == ["login"]


async def test_acp_provider_auth_required_without_any_method_error_omits_advertised_clause() -> (
    None
):
    agent = AuthRequiredACPAgent(auth_methods=[])
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    with pytest.raises(UserError) as exc_info:
        await provider.ensure_session()

    assert "(advertised:" not in str(exc_info.value)


async def test_acp_provider_requires_client_setup_for_env_var_auth_method() -> None:
    agent = AuthRequiredACPAgent(
        auth_methods=[
            EnvVarAuthMethod(
                id="api-key",
                name="API key",
                type="env_var",
                vars=[AuthEnvVar(name="API_KEY")],
            )
        ],
    )
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    with pytest.raises(UserError, match="require client-side setup"):
        await provider.ensure_session()

    assert agent.authenticated_with == []


async def test_acp_provider_ensure_session_forwards_model_name() -> None:
    agent = EchoACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")

    await provider.ensure_session(model_name="agent")

    assert agent.session_models == [("session-1", "agent")]


async def test_acp_provider_set_session_mode_supports_synchronous_agent_method() -> None:
    mode_calls: list[tuple[str, str]] = []

    class SyncModeACPAgent(EchoACPAgent):
        def set_session_mode(self, session_id: str, mode_id: str, **kwargs: Any) -> None:
            del kwargs
            mode_calls.append((session_id, mode_id))

    provider = AcpProvider(acp_agent=cast(AcpAgent, SyncModeACPAgent()), cwd="/workspace")

    await provider.set_session_mode("default")

    assert mode_calls == [("session-1", "default")]


async def test_acp_provider_set_session_mode_reuses_session_created_by_prior_prompt() -> None:
    class ModeACPAgent(EchoACPAgent):
        def __init__(self) -> None:
            super().__init__()
            self.mode_calls: list[tuple[str, str]] = []

        async def set_session_mode(
            self,
            session_id: str,
            mode_id: str,
            **kwargs: Any,
        ) -> None:
            del kwargs
            self.mode_calls.append((session_id, mode_id))

    agent = ModeACPAgent()
    provider = AcpProvider(acp_agent=cast(AcpAgent, agent), cwd="/workspace")
    model = provider.model()

    await model.request(
        [ModelRequest(parts=[UserPromptPart("hello")])],
        None,
        ModelRequestParameters(),
    )
    await provider.set_session_mode("default")

    assert agent.mode_calls == [(provider.session_id, "default")]
    assert agent.session_cwds == ["/workspace"]


def test_acp_provider_raise_on_empty_turn_property_reflects_constructor_arg() -> None:
    default_provider = AcpProvider(acp_agent=cast(AcpAgent, EchoACPAgent()), cwd="/workspace")
    assert default_provider.raise_on_empty_turn is False

    opted_in_provider = AcpProvider(
        acp_agent=cast(AcpAgent, EchoACPAgent()),
        cwd="/workspace",
        raise_on_empty_turn=True,
    )
    assert opted_in_provider.raise_on_empty_turn is True


async def test_empty_turn_raises_with_stop_reason_in_message_when_available() -> None:
    class RefusalACPAgent(NoHandshakeACPAgent):
        async def prompt(
            self,
            prompt: list[Any],
            session_id: str,
            message_id: str | None = None,
            **kwargs: Any,
        ) -> PromptResponse:
            del prompt, session_id, message_id, kwargs
            return PromptResponse(stop_reason="refusal")

    provider = AcpProvider(
        acp_agent=cast(AcpAgent, RefusalACPAgent()),
        cwd="/workspace",
        raise_on_empty_turn=True,
    )
    model = provider.model()

    with pytest.raises(UnexpectedModelBehavior, match=r"stop reason: refusal"):
        await model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(),
        )


# ---------------------------------------------------------------------------
# Agent error propagation (anyio TaskGroup unwrapping)
# ---------------------------------------------------------------------------


def test_unwrap_acp_error_peels_single_child_group() -> None:
    inner = RequestError(-32000, "Rate limited")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert client_module._unwrap_acp_error(group) is inner


def test_unwrap_acp_error_peels_nested_single_child_groups() -> None:
    inner = RequestError(-32000, "Rate limited")
    nested = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [ExceptionGroup("unhandled errors in a TaskGroup", [inner])],
    )
    assert client_module._unwrap_acp_error(nested) is inner


def test_unwrap_acp_error_drops_taskgroup_context_noise() -> None:
    err = RuntimeError("boom")
    err.__context__ = ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("x")])
    cleaned = client_module._unwrap_acp_error(err)
    assert cleaned is err
    assert cleaned.__context__ is None
    assert cleaned.__suppress_context__ is True


def test_unwrap_acp_error_leaves_plain_exception_untouched() -> None:
    plain = ValueError("z")
    assert client_module._unwrap_acp_error(plain) is plain


def test_unwrap_acp_error_keeps_multi_child_group() -> None:
    group = ExceptionGroup("many", [ValueError("a"), RequestError(-32000, "b")])
    assert client_module._unwrap_acp_error(group) is group


def test_unwrap_acp_error_preserves_unrelated_single_child_group_and_context() -> None:
    context = ExceptionGroup("validation failures", [ValueError("context")])
    inner = RuntimeError("boom")
    inner.__context__ = context
    group = ExceptionGroup("validation failures", [inner])

    assert client_module._unwrap_acp_error(group) is group
    assert inner.__context__ is context


async def test_request_prompt_unwraps_taskgroup_error_from_the_agent() -> None:
    class GroupErrorACPAgent(EchoACPAgent):
        async def prompt(
            self,
            prompt: list[Any],
            session_id: str,
            message_id: str | None = None,
            **kwargs: Any,
        ) -> PromptResponse:
            del prompt, session_id, message_id, kwargs
            raise ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [RequestError(-32000, "Rate limited")],
            )

    provider = AcpProvider(acp_agent=cast(AcpAgent, GroupErrorACPAgent()), cwd="/workspace")

    with pytest.raises(RequestError, match="Rate limited"):
        await provider.request_prompt(
            model_name=None,
            prompt=[text_block("hi")],
            model_request_parameters=ModelRequestParameters(),
        )


async def test_request_prompt_reraises_cancellederror_untouched() -> None:
    class CancellingACPAgent(EchoACPAgent):
        async def prompt(
            self,
            prompt: list[Any],
            session_id: str,
            message_id: str | None = None,
            **kwargs: Any,
        ) -> PromptResponse:
            del prompt, session_id, message_id, kwargs
            raise asyncio.CancelledError

    provider = AcpProvider(acp_agent=cast(AcpAgent, CancellingACPAgent()), cwd="/workspace")

    with pytest.raises(asyncio.CancelledError):
        await provider.request_prompt(
            model_name=None,
            prompt=[text_block("hi")],
            model_request_parameters=ModelRequestParameters(),
        )
