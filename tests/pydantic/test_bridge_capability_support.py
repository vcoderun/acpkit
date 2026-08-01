from __future__ import annotations as _annotations

import asyncio
import builtins
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pydantic_acp.bridges.capability_support as capability_support_module
import pytest
from acp.schema import ToolKind
from pydantic_acp import (
    HarnessCodeModeBridge,
    HarnessFileSystemBridge,
    HarnessShellBridge,
)
from pydantic_acp.bridges.capability_support import (
    _json_user_location,
    _resolve_mcp_server_id,
)
from pydantic_acp.projection import (
    HarnessCodeModeProjectionMap,
    HarnessFileSystemProjectionMap,
    HarnessShellProjectionMap,
)
from pydantic_acp.session.state import utc_now
from pydantic_ai import ModelRequestContext, ModelResponse
from pydantic_ai.capabilities import MCP, ImageGeneration, Toolset
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.toolsets.function import FunctionToolset

from .support import (
    AcpSessionContext,
    AdapterConfig,
    Agent,
    AgentBridgeBuilder,
    AnthropicCompactionBridge,
    ImageGenerationBridge,
    IncludeToolReturnSchemasBridge,
    McpCapabilityBridge,
    MemorySessionStore,
    OpenAICompactionBridge,
    Path,
    PrefixToolsBridge,
    RecordingClient,
    SetToolMetadataBridge,
    TestModel,
    ThreadExecutorBridge,
    ToolCallProgress,
    ToolCallStart,
    ToolsetBridge,
    create_acp_agent,
    text_block,
)


class _FakeHarnessCapability:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _install_fake_harness_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    harness_module = ModuleType("pydantic_ai_harness")
    filesystem_module = ModuleType("pydantic_ai_harness.filesystem")
    shell_module = ModuleType("pydantic_ai_harness.shell")
    code_mode_module = ModuleType("pydantic_ai_harness.code_mode")

    cast("Any", filesystem_module).FileSystem = _FakeHarnessCapability
    cast("Any", shell_module).Shell = _FakeHarnessCapability
    cast("Any", code_mode_module).CodeMode = _FakeHarnessCapability

    monkeypatch.setitem(sys.modules, "pydantic_ai_harness", harness_module)
    monkeypatch.setitem(sys.modules, "pydantic_ai_harness.filesystem", filesystem_module)
    monkeypatch.setitem(sys.modules, "pydantic_ai_harness.shell", shell_module)
    monkeypatch.setitem(sys.modules, "pydantic_ai_harness.code_mode", code_mode_module)


def _test_session() -> AcpSessionContext:
    now = utc_now()
    return AcpSessionContext(
        session_id="s",
        cwd=Path("/workspace"),
        created_at=now,
        updated_at=now,
    )


def _write_mcp_stdio_server_script(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "from __future__ import annotations as _annotations",
                "",
                "from mcp.server.fastmcp import FastMCP",
                "",
                'mcp = FastMCP("test-mcp", instructions="Be a helpful assistant.")',
                "",
                "@mcp.tool()",
                "def ping() -> str:",
                '    return "pong"',
                "",
                'if __name__ == "__main__":',
                '    mcp.run("stdio")',
                "",
            ),
        ),
        encoding="utf-8",
    )


def _build_mcp_stdio_test_env(
    *,
    executable: str,
    base_executable: str | None,
    sys_path: list[str],
    environ: dict[str, str],
) -> tuple[str, dict[str, str]]:
    executable_path = Path(executable)
    python_executable = (
        str(executable_path) if executable_path.exists() else base_executable or executable
    )
    python_path_entries = [entry for entry in sys_path if entry]
    mcp_env = dict(environ)
    if python_path_entries:
        existing_pythonpath = mcp_env.get("PYTHONPATH")
        combined_pythonpath = os.pathsep.join(
            (
                *python_path_entries,
                *([existing_pythonpath] if existing_pythonpath else []),
            ),
        )
        mcp_env["PYTHONPATH"] = combined_pythonpath
    return python_executable, mcp_env


def test_harness_filesystem_bridge_builds_capability_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_harness_modules(monkeypatch)
    session = _test_session()
    bridge = HarnessFileSystemBridge(
        root_dir=Path("/workspace"),
        allowed_patterns=("src/**",),
        denied_patterns=("*.secret",),
        protected_patterns=(".git/*", ".env"),
        max_read_lines=50,
        max_search_results=12,
        max_find_results=7,
    )

    capability = bridge.build_capability(session)
    capability_tuple = bridge.build_agent_capabilities(session)
    assert isinstance(capability, _FakeHarnessCapability)
    assert capability_tuple is not None
    assert isinstance(capability_tuple[0], _FakeHarnessCapability)
    assert capability.kwargs == {
        "root_dir": Path("/workspace"),
        "allowed_patterns": ["src/**"],
        "denied_patterns": ["*.secret"],
        "max_read_lines": 50,
        "max_search_results": 12,
        "max_find_results": 7,
        "protected_patterns": [".git/*", ".env"],
    }
    assert isinstance(bridge.get_projection_maps()[0], HarnessFileSystemProjectionMap)
    assert bridge.get_session_metadata(session, cast("Any", object())) == {
        "allowed_patterns": ["src/**"],
        "denied_patterns": ["*.secret"],
        "max_find_results": 7,
        "max_read_lines": 50,
        "max_search_results": 12,
        "protected_patterns": [".env", ".git/*"],
        "root_dir": "/workspace",
        "tool_names": [
            "create_directory",
            "edit_file",
            "list_directory",
            "read_file",
            "search_files",
            "write_file",
        ],
    }
    assert bridge.get_tool_kind("read_file") == "read"
    assert bridge.get_tool_kind("write_file") == "edit"
    assert bridge.get_tool_kind("edit_file") == "edit"
    assert bridge.get_tool_kind("search_files") == "search"
    assert bridge.get_tool_kind("other") is None


def test_harness_shell_bridge_builds_capability_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_harness_modules(monkeypatch)
    session = _test_session()
    bridge = HarnessShellBridge(
        cwd=Path("/workspace"),
        allowed_commands=("git", "pytest"),
        denied_commands=("rm",),
        denied_operators=(">",),
        default_timeout=2.5,
        max_output_chars=123,
        persist_cwd=True,
        allow_interactive=True,
        env={"SAFE": "1"},
        denied_env_patterns=("OPENAI_*",),
    )

    capability = bridge.build_capability(session)
    capability_tuple = bridge.build_agent_capabilities(session)
    assert isinstance(capability, _FakeHarnessCapability)
    assert capability_tuple is not None
    assert isinstance(capability_tuple[0], _FakeHarnessCapability)
    assert capability.kwargs == {
        "cwd": Path("/workspace"),
        "allowed_commands": ["git", "pytest"],
        "denied_operators": [">"],
        "default_timeout": 2.5,
        "max_output_chars": 123,
        "persist_cwd": True,
        "allow_interactive": True,
        "env": {"SAFE": "1"},
        "denied_env_patterns": ["OPENAI_*"],
        "denied_commands": ["rm"],
    }
    assert isinstance(bridge.get_projection_maps()[0], HarnessShellProjectionMap)
    assert bridge.get_session_metadata(session, cast("Any", object())) == {
        "allow_interactive": True,
        "allowed_commands": ["git", "pytest"],
        "cwd": "/workspace",
        "default_timeout": 2.5,
        "denied_commands": ["rm"],
        "denied_env_patterns": ["OPENAI_*"],
        "denied_operators": [">"],
        "env_keys": ["SAFE"],
        "max_output_chars": 123,
        "persist_cwd": True,
        "tool_names": ["check_command", "run_command", "start_command", "stop_command"],
    }
    assert bridge.get_tool_kind("run_command") == "execute"
    assert bridge.get_tool_kind("not_shell") is None


def test_harness_bridges_omit_none_optional_constructor_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_harness_modules(monkeypatch)
    session = _test_session()

    filesystem_capability = HarnessFileSystemBridge().build_capability(session)
    shell_capability = HarnessShellBridge().build_capability(session)

    assert isinstance(filesystem_capability, _FakeHarnessCapability)
    assert isinstance(shell_capability, _FakeHarnessCapability)
    assert "protected_patterns" not in filesystem_capability.kwargs
    assert "denied_commands" not in shell_capability.kwargs


def test_harness_code_mode_bridge_builds_capability_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_harness_modules(monkeypatch)
    session = _test_session()
    os_access = object()
    mount = object()
    bridge = HarnessCodeModeBridge(
        max_retries=5,
        os_access=os_access,
        mount=mount,
        dynamic_catalog=True,
    )

    capability = bridge.build_capability(session)
    capability_tuple = bridge.build_agent_capabilities(session)
    assert isinstance(capability, _FakeHarnessCapability)
    assert capability_tuple is not None
    assert isinstance(capability_tuple[0], _FakeHarnessCapability)
    assert capability.kwargs == {
        "tools": "all",
        "max_retries": 5,
        "os_access": os_access,
        "mount": mount,
        "dynamic_catalog": True,
    }
    assert isinstance(bridge.get_projection_maps()[0], HarnessCodeModeProjectionMap)
    assert bridge.get_session_metadata(session, cast("Any", object())) == {
        "dynamic_catalog": True,
        "has_mount": True,
        "has_os_access": True,
        "max_retries": 5,
        "tool_names": ["run_code"],
    }
    assert bridge.get_tool_kind("run_code") == "execute"
    assert bridge.get_tool_kind("other") is None


def test_harness_v07_bridges_build_real_capabilities() -> None:
    pytest.importorskip("pydantic_ai_harness")

    from pydantic_ai_harness.code_mode import CodeMode
    from pydantic_ai_harness.filesystem import FileSystem
    from pydantic_ai_harness.shell import Shell

    session = _test_session()

    assert isinstance(
        HarnessFileSystemBridge(root_dir=Path(".")).build_capability(session),
        FileSystem,
    )
    assert isinstance(
        HarnessShellBridge(cwd=Path(".")).build_capability(session),
        Shell,
    )
    assert isinstance(HarnessCodeModeBridge().build_capability(session), CodeMode)


def test_harness_bridges_report_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pydantic_ai_harness", ModuleType("pydantic_ai_harness"))
    for module_name in tuple(sys.modules):
        if module_name.startswith("pydantic_ai_harness"):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    real_import = builtins.__import__

    def missing_harness_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name.startswith("pydantic_ai_harness"):
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    assert missing_harness_import("math").__name__ == "math"
    monkeypatch.setattr(builtins, "__import__", missing_harness_import)
    session = _test_session()

    with pytest.raises(ImportError, match="pydantic-ai-harness"):
        HarnessFileSystemBridge().build_capability(session)
    with pytest.raises(ImportError, match="pydantic-ai-harness"):
        HarnessShellBridge().build_capability(session)
    with pytest.raises(ImportError, match=r"pydantic-ai-harness\[code-mode\]"):
        HarnessCodeModeBridge().build_capability(session)


def test_harness_bridge_projection_maps_are_added_to_adapter_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_harness_modules(monkeypatch)
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(
            capability_bridges=[
                HarnessFileSystemBridge(),
                HarnessShellBridge(),
                HarnessCodeModeBridge(),
            ],
            session_store=MemorySessionStore(),
        ),
    )

    config = cast("Any", adapter)._config
    projection_map_types = {type(projection_map) for projection_map in config.projection_maps}
    assert HarnessFileSystemProjectionMap in projection_map_types
    assert HarnessShellProjectionMap in projection_map_types
    assert HarnessCodeModeProjectionMap in projection_map_types

    classifier = cast("Any", adapter)._tool_classifier
    assert cast("ToolKind", classifier.classify("read_file")) == "read"
    assert cast("ToolKind", classifier.classify("run_command")) == "execute"
    assert cast("ToolKind", classifier.classify("run_code")) == "execute"


def test_thread_executor_bridge_runs_sync_tools_on_configured_executor(
    tmp_path: Path,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="acpkit-bridge")
    bridge = ThreadExecutorBridge(executor=executor)

    try:

        def factory(session: AcpSessionContext) -> Agent[None, str]:
            builder = AgentBridgeBuilder(
                session=session,
                capability_bridges=[bridge],
            )
            contributions = builder.build()
            agent = Agent(
                TestModel(call_tools=["check_thread"], custom_output_text="done"),
                capabilities=contributions.capabilities,
            )

            @agent.tool_plain
            def check_thread() -> str:
                return threading.current_thread().name

            return agent

        adapter = create_acp_agent(
            agent_factory=factory,
            config=AdapterConfig(
                capability_bridges=[bridge],
                session_store=MemorySessionStore(),
            ),
        )
        client = RecordingClient()
        adapter.on_connect(client)

        session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
        response = asyncio.run(
            adapter.prompt(
                prompt=[text_block("Check the thread executor.")],
                session_id=session.session_id,
            ),
        )

        assert response.stop_reason == "end_turn"
        progress_update = next(
            update
            for _, update in client.updates
            if isinstance(update, ToolCallProgress) and update.title == "check_thread"
        )
        assert isinstance(progress_update.raw_output, str)
        assert progress_update.raw_output.startswith("acpkit-bridge")
    finally:
        executor.shutdown(wait=True)


def test_thread_executor_bridge_supports_the_legacy_capability_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1)

    class LegacyThreadExecutor:
        def __init__(self, configured_executor: ThreadPoolExecutor) -> None:
            self.executor = configured_executor

    monkeypatch.setitem(
        capability_support_module.pydantic_capabilities.__dict__,
        "UseThreadExecutor",
        None,
    )
    monkeypatch.setitem(
        capability_support_module.pydantic_capabilities.__dict__,
        "ThreadExecutor",
        LegacyThreadExecutor,
    )

    try:
        bridge = ThreadExecutorBridge(executor=executor)
        capability = bridge.build_capability(cast("AcpSessionContext", object()))

        assert cast("Any", capability).executor is executor
    finally:
        executor.shutdown(wait=True)


def test_metadata_and_return_schema_bridges_modify_selected_tools(
    tmp_path: Path,
) -> None:
    test_model = TestModel(custom_output_text="done")
    bridges = [
        SetToolMetadataBridge(tools=["tool_a"], code_mode=True),
        IncludeToolReturnSchemasBridge(tools=["tool_a"]),
    ]

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=bridges,
        )
        contributions = builder.build()
        agent = Agent(
            test_model,
            capabilities=contributions.capabilities,
        )

        @agent.tool_plain
        def tool_a(x: int) -> int:
            return x

        @agent.tool_plain
        def tool_b(x: str) -> str:
            return x

        return agent

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(
            capability_bridges=bridges,
            session_store=MemorySessionStore(),
        ),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Inspect the available tools.")],
            session_id=session.session_id,
        ),
    )

    assert response.stop_reason == "end_turn"
    params = test_model.last_model_request_parameters
    assert params is not None
    tool_a_def = next(tool_def for tool_def in params.function_tools if tool_def.name == "tool_a")
    tool_b_def = next(tool_def for tool_def in params.function_tools if tool_def.name == "tool_b")
    assert tool_a_def.metadata is not None
    assert tool_a_def.metadata["code_mode"] is True
    assert tool_a_def.include_return_schema is True
    assert "Return schema" in (tool_a_def.description or "")
    assert tool_b_def.metadata is None or "code_mode" not in tool_b_def.metadata
    assert tool_b_def.include_return_schema is not True
    assert "Return schema" not in (tool_b_def.description or "")


def test_set_tool_metadata_bridge_can_attach_metadata_to_non_schema_tool(
    tmp_path: Path,
) -> None:
    test_model = TestModel(custom_output_text="done")
    bridge = SetToolMetadataBridge(tools=["tool_b"], code_mode=False)

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[bridge],
        )
        contributions = builder.build()
        agent = Agent(
            test_model,
            capabilities=contributions.capabilities,
        )

        @agent.tool_plain
        def tool_b(x: str) -> str:
            return x

        return agent

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(
            capability_bridges=[bridge],
            session_store=MemorySessionStore(),
        ),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Inspect the available tools.")],
            session_id=session.session_id,
        ),
    )

    assert response.stop_reason == "end_turn"
    params = test_model.last_model_request_parameters
    assert params is not None
    tool_b_def = next(tool_def for tool_def in params.function_tools if tool_def.name == "tool_b")
    assert tool_b_def.metadata is not None
    assert "code_mode" in tool_b_def.metadata


def test_image_generation_and_mcp_capability_bridges_build_metadata_and_classification() -> None:
    session = AcpSessionContext(
        session_id="session-1",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    image_bridge = ImageGenerationBridge(
        fallback_model="openai-responses:gpt-5.4",
        quality="high",
        size="1024x1024",
        output_format="png",
    )
    mcp_bridge = McpCapabilityBridge(
        url="https://example.com/services/repo/sse",
        local=False,
        allowed_tools=["search", "read_file"],
        description="Repo MCP",
        authorization_token="secret",
    )

    image_capability = image_bridge.build_capability(session)
    mcp_capability = mcp_bridge.build_capability(session)

    assert isinstance(image_capability, ImageGeneration)
    assert isinstance(mcp_capability, MCP)
    assert image_bridge.get_tool_kind("image_generation") == "execute"
    assert image_bridge.get_tool_kind("generate_image") == "execute"
    assert image_bridge.get_session_metadata(session, Agent(TestModel())) == {
        "aspect_ratio": None,
        "background": None,
        "fallback_model": "openai-responses:gpt-5.4",
        "input_fidelity": None,
        "moderation": None,
        "output_compression": None,
        "output_format": "png",
        "quality": "high",
        "size": "1024x1024",
        "tool_names": ["generate_image", "image_generation"],
    }
    mcp_metadata = mcp_bridge.get_session_metadata(session, Agent(TestModel()))
    assert mcp_bridge.get_tool_kind("mcp_server:repo") == "execute"
    assert mcp_metadata["allowed_tools"] == ["read_file", "search"]
    assert mcp_metadata["description"] == "Repo MCP"
    assert mcp_metadata["has_authorization_token"] is True
    assert mcp_metadata["headers"] == []
    assert mcp_metadata["server_id"] == "example.com-sse"
    assert mcp_metadata["url"] == "https://example.com/services/repo/sse"


def test_toolset_and_prefix_bridges_expose_function_tools_to_the_model(
    tmp_path: Path,
) -> None:
    toolset = FunctionToolset()

    @toolset.tool_plain
    def lookup(query: str) -> str:
        return f"lookup:{query}"

    prefixed_toolset = FunctionToolset()

    @prefixed_toolset.tool_plain
    def search(term: str) -> str:
        return f"search:{term}"

    plain_model = TestModel(custom_output_text="done")
    prefixed_model = TestModel(custom_output_text="done")

    def plain_factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[ToolsetBridge(toolset=toolset)],
        )
        contributions = builder.build()
        return Agent(
            plain_model,
            capabilities=contributions.capabilities,
        )

    def prefixed_factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[
                PrefixToolsBridge(
                    wrapped=Toolset(toolset=prefixed_toolset),
                    prefix="repo",
                ),
            ],
        )
        contributions = builder.build()
        return Agent(
            prefixed_model,
            capabilities=contributions.capabilities,
        )

    plain_adapter = create_acp_agent(
        agent_factory=plain_factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    plain_session = asyncio.run(plain_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    plain_response = asyncio.run(
        plain_adapter.prompt(
            prompt=[text_block("Inspect tools.")],
            session_id=plain_session.session_id,
        ),
    )
    assert plain_response.stop_reason == "end_turn"
    assert plain_model.last_model_request_parameters is not None
    assert [tool.name for tool in plain_model.last_model_request_parameters.function_tools] == [
        "lookup",
    ]

    prefixed_adapter = create_acp_agent(
        agent_factory=prefixed_factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    prefixed_session = asyncio.run(prefixed_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prefixed_response = asyncio.run(
        prefixed_adapter.prompt(
            prompt=[text_block("Inspect prefixed tools.")],
            session_id=prefixed_session.session_id,
        ),
    )
    assert prefixed_response.stop_reason == "end_turn"
    assert prefixed_model.last_model_request_parameters is not None
    assert [tool.name for tool in prefixed_model.last_model_request_parameters.function_tools] == [
        "repo_search",
    ]


def test_toolset_bridge_preserves_instruction_parts_and_ordering(
    tmp_path: Path,
) -> None:
    user_toolset = FunctionToolset(instructions="User capability instructions.")
    bridge_toolset = FunctionToolset(instructions=lambda: "Bridge toolset instructions.")
    model = TestModel(custom_output_text="done")

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[ToolsetBridge(toolset=bridge_toolset)],
        )
        contributions = builder.build(capabilities=(Toolset(toolset=user_toolset),))
        return Agent(
            model,
            capabilities=contributions.capabilities,
        )

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Inspect toolset instructions.")],
            session_id=session.session_id,
        ),
    )

    assert response.stop_reason == "end_turn"
    params = model.last_model_request_parameters
    assert params is not None
    assert params.instruction_parts is not None
    assert [(part.content, part.dynamic) for part in params.instruction_parts] == [
        ("User capability instructions.", False),
        ("Bridge toolset instructions.", True),
    ]


def test_mcp_toolset_include_instructions_reaches_model_request(tmp_path: Path) -> None:
    def return_instructions(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        return ModelResponse(parts=[TextPart(info.instructions or "")])

    toolset = FunctionToolset(instructions="Be a helpful assistant.")

    @toolset.tool_plain
    def ping() -> str:
        return "pong"

    assert ping() == "pong"

    agent = Agent(
        FunctionModel(return_instructions),
        toolsets=[toolset],
    )
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Inspect MCP instructions.")],
            session_id=session.session_id,
        ),
    )

    assert response.stop_reason == "end_turn" and "Be a helpful assistant." in "".join(
        update.content.text
        for _, update in client.updates
        if getattr(update, "sessionUpdate", None) == "agent_message_chunk"
    )


def test_mcp_stdio_test_helpers_cover_script_and_env_fallbacks(tmp_path: Path) -> None:
    server_script = tmp_path / "mcp_stdio_server.py"
    _write_mcp_stdio_server_script(server_script)
    script_text = server_script.read_text(encoding="utf-8")
    assert script_text.startswith("from __future__ import annotations as _annotations")
    assert 'instructions="Be a helpful assistant."' in script_text

    existing_executable = tmp_path / "python"
    existing_executable.write_text("", encoding="utf-8")
    existing_python, existing_env = _build_mcp_stdio_test_env(
        executable=str(existing_executable),
        base_executable="/fallback-python",
        sys_path=["/repo/src", "", "/repo/tests"],
        environ={"PYTHONPATH": "/already/set"},
    )
    assert existing_python == str(existing_executable)
    assert existing_env["PYTHONPATH"] == os.pathsep.join(
        ("/repo/src", "/repo/tests", "/already/set"),
    )

    missing_python, missing_env = _build_mcp_stdio_test_env(
        executable=str(tmp_path / "missing-python"),
        base_executable="/fallback-python",
        sys_path=[""],
        environ={},
    )
    assert missing_python == "/fallback-python"
    assert "PYTHONPATH" not in missing_env

    raw_python, raw_env = _build_mcp_stdio_test_env(
        executable="/raw-python",
        base_executable=None,
        sys_path=[],
        environ={"PATH": "/usr/bin"},
    )
    assert raw_python == "/raw-python"
    assert raw_env == {"PATH": "/usr/bin"}


def test_capability_bridge_helper_and_metadata_edge_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AcpSessionContext(
        session_id="session-2",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    agent = Agent(TestModel())
    image_bridge = ImageGenerationBridge()
    mcp_bridge = McpCapabilityBridge(
        url="https://example.com/",
        id="repo-server",
        headers={"authorization": "Bearer x"},
    )
    toolset_bridge = ToolsetBridge(toolset=cast("Any", SimpleNamespace(id=object())))
    prefix_bridge = PrefixToolsBridge(wrapped=Toolset(toolset=FunctionToolset()), prefix="repo")
    openai_bridge = OpenAICompactionBridge(message_count_threshold=4, instructions="compact")
    anthropic_bridge = AnthropicCompactionBridge(
        token_threshold=10,
        instructions="compact",
        pause_after_compaction=True,
    )

    assert _resolve_mcp_server_id("https://example.com/tools/sse", "explicit-id") == "explicit-id"
    assert _resolve_mcp_server_id("https://example.com/", None) == "example.com"
    assert _resolve_mcp_server_id("urn:acpkit", None) == "acpkit"
    assert _json_user_location(cast("Any", {"city": "Istanbul", "ignored": object()})) == {
        "city": "Istanbul",
    }

    assert len(image_bridge.build_agent_capabilities(session)) == 1
    assert len(toolset_bridge.build_agent_capabilities(session)) == 1
    assert len(prefix_bridge.build_agent_capabilities(session)) == 1
    assert len(openai_bridge.build_agent_capabilities(session)) == 1
    assert openai_bridge.get_session_metadata(session, agent) == {
        "has_trigger": False,
        "instructions": "compact",
        "message_count_threshold": 4,
    }
    assert toolset_bridge.get_session_metadata(session, agent) == {
        "toolset_id": None,
        "toolset_type": "SimpleNamespace",
    }
    assert prefix_bridge.get_session_metadata(session, agent) == {
        "prefix": "repo",
        "wrapped_capability": "Toolset",
    }
    assert prefix_bridge.get_tool_kind("search") is None
    assert mcp_bridge.get_session_metadata(session, agent)["server_id"] == "repo-server"
    assert mcp_bridge.get_tool_kind("repo.search") is None
    monkeypatch.setattr(mcp_bridge, "build_capability", lambda session: object())
    monkeypatch.setattr(anthropic_bridge, "build_capability", lambda session: object())
    assert len(mcp_bridge.build_agent_capabilities(session)) == 1
    assert len(anthropic_bridge.build_agent_capabilities(session)) == 1
    assert anthropic_bridge.get_session_metadata(session, agent) == {
        "instructions": "compact",
        "pause_after_compaction": True,
        "token_threshold": 10,
    }


def test_provider_specific_compaction_bridges_build_capabilities_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AcpSessionContext(
        session_id="session-2",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    openai_bridge = OpenAICompactionBridge(
        message_count_threshold=10,
        instructions="Compact aggressively.",
    )
    anthropic_bridge = AnthropicCompactionBridge(
        token_threshold=90_000,
        instructions="Compact safely.",
        pause_after_compaction=True,
    )

    class AnthropicCompaction:
        pass

    openai_capability = openai_bridge.build_capability(session)
    assert openai_capability.get_serialization_name() == "OpenAICompaction"
    monkeypatch.setattr(
        anthropic_bridge,
        "build_capability",
        lambda session: AnthropicCompaction(),
    )
    anthropic_capability = anthropic_bridge.build_capability(session)
    assert anthropic_capability.__class__.__name__ == "AnthropicCompaction"
    assert openai_bridge.get_session_metadata(session, Agent(TestModel())) == {
        "has_trigger": False,
        "instructions": "Compact aggressively.",
        "message_count_threshold": 10,
    }


def test_capability_bridge_helper_paths_cover_import_error_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AcpSessionContext(
        session_id="session-error",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    agent = Agent(TestModel())
    mcp_bridge = McpCapabilityBridge(url="https://example.com/")
    anthropic_bridge = AnthropicCompactionBridge(
        token_threshold=10,
        instructions="compact",
        pause_after_compaction=True,
    )

    monkeypatch.setattr(
        mcp_bridge,
        "build_capability",
        lambda session: (_ for _ in ()).throw(ImportError("mcp not installed")),
    )
    monkeypatch.setattr(
        anthropic_bridge,
        "build_capability",
        lambda session: (_ for _ in ()).throw(ImportError("anthropic missing")),
    )

    with pytest.raises(ImportError, match="mcp"):
        mcp_bridge.build_capability(session)
    with pytest.raises(ImportError, match="anthropic"):
        anthropic_bridge.build_capability(session)

    with pytest.raises(ImportError, match="mcp"):
        mcp_bridge.build_agent_capabilities(session)

    with pytest.raises(ImportError, match="anthropic"):
        anthropic_bridge.build_agent_capabilities(session)

    with pytest.raises(ImportError, match="anthropic"):
        anthropic_bridge.build_capability(session)

    assert anthropic_bridge.get_session_metadata(session, agent) == {
        "instructions": "compact",
        "pause_after_compaction": True,
        "token_threshold": 10,
    }


def test_anthropic_compaction_bridge_can_build_with_stubbed_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AcpSessionContext(
        session_id="session-anthropic",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    bridge = AnthropicCompactionBridge(
        token_threshold=12,
        instructions="Compact.",
        pause_after_compaction=True,
    )

    class StubAnthropicCompaction:
        def __init__(
            self,
            *,
            token_threshold: int,
            instructions: str | None,
            pause_after_compaction: bool,
        ) -> None:
            self.token_threshold = token_threshold
            self.instructions = instructions
            self.pause_after_compaction = pause_after_compaction

    monkeypatch.setitem(
        sys.modules,
        "pydantic_ai.models.anthropic",
        SimpleNamespace(AnthropicCompaction=StubAnthropicCompaction),
    )

    capability = bridge.build_capability(session)
    assert isinstance(capability, StubAnthropicCompaction)
    assert capability.token_threshold == 12


def test_openai_compaction_bridge_records_visible_start_and_completion() -> None:
    session = AcpSessionContext(
        session_id="session-3",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    bridge = OpenAICompactionBridge(
        message_count_threshold=1,
        instructions="Compact aggressively.",
    )
    capability = bridge.build_capability(session)
    fake_model = cast("Any", object.__new__(_FakeOpenAIResponsesModel))
    request_context = ModelRequestContext(
        model=fake_model,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="old")]),
            ModelRequest(parts=[UserPromptPart(content="new")]),
        ],
        model_settings=None,
        model_request_parameters=cast("Any", SimpleNamespace()),
    )

    updated_context = asyncio.run(
        capability.before_model_request(
            cast("Any", SimpleNamespace()),
            request_context,
        ),
    )

    assert len(updated_context.messages) == 2
    updates = bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None
    assert len(updates) == 2
    start_update = updates[0]
    progress_update = updates[1]
    assert isinstance(start_update, ToolCallStart)
    assert isinstance(progress_update, ToolCallProgress)
    assert start_update.title == "Context Compaction"
    assert start_update.status == "in_progress"
    assert start_update.raw_input == {
        "provider": "openai",
        "instructions": "Compact aggressively.",
        "message_count": 2,
    }
    assert progress_update.tool_call_id == start_update.tool_call_id
    assert progress_update.status == "completed"
    assert progress_update.raw_output == "\n".join(
        (
            "Provider: openai",
            "Status: history compacted",
            "Compaction payload stored for round-trip.",
            "Compaction id: cmp-123",
        ),
    )


class _FakeOpenAIResponsesModel(OpenAIResponsesModel):
    async def compact_messages(
        self,
        request_context: ModelRequestContext,
        *,
        instructions: str | None = None,
    ) -> ModelResponse:
        del request_context, instructions
        from pydantic_ai import CompactionPart

        return ModelResponse(
            parts=[CompactionPart(id="cmp-123", provider_name="openai", provider_details={})],
        )


class _FailingOpenAIResponsesModel(OpenAIResponsesModel):
    async def compact_messages(
        self,
        request_context: ModelRequestContext,
        *,
        instructions: str | None = None,
    ) -> ModelResponse:
        del request_context, instructions
        raise RuntimeError("boom")


def test_openai_compaction_helpers_cover_trigger_threshold_and_missing_parts() -> None:
    from pydantic_acp.bridges.capability_support import (
        _extract_compaction_part,
        _format_openai_compaction_output,
        _should_openai_compact,
    )

    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="one")]),
        ModelRequest(parts=[UserPromptPart(content="two")]),
    ]

    assert (
        _should_openai_compact(
            messages,
            trigger=lambda payload: len(payload) == 2,
            message_count_threshold=None,
        )
        is True
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=lambda payload: len(payload) > 10,
            message_count_threshold=0,
        )
        is False
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=None,
            message_count_threshold=1,
        )
        is True
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=None,
            message_count_threshold=5,
        )
        is False
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=None,
            message_count_threshold=None,
        )
        is False
    )

    request_context = ModelRequestContext(
        model=cast("Any", object.__new__(_FakeOpenAIResponsesModel)),
        messages=[
            ModelRequest(parts=[UserPromptPart(content="prompt")]),
            ModelResponse(parts=[]),
            ModelResponse(
                parts=[CompactionPart(id=None, provider_name="openai", provider_details={})],
            ),
        ],
        model_settings=None,
        model_request_parameters=cast("Any", SimpleNamespace()),
    )

    compacted_part = _extract_compaction_part(request_context.messages)
    assert compacted_part is not None
    assert compacted_part.id is None
    assert _format_openai_compaction_output(request_context) == "\n".join(
        (
            "Provider: openai",
            "Status: history compacted",
            "Compaction payload stored for round-trip.",
        ),
    )

    assert (
        _extract_compaction_part([ModelRequest(parts=[UserPromptPart(content="prompt")])]) is None
    )
    assert (
        _extract_compaction_part(
            [
                ModelResponse(
                    parts=[
                        TextPart(content="skip"),
                        CompactionPart(
                            id="cmp-456",
                            provider_name="openai",
                            provider_details={},
                        ),
                    ],
                ),
            ],
        )
        is not None
    )


def test_openai_compaction_bridge_skips_when_not_needed_and_records_failures() -> None:
    session = AcpSessionContext(
        session_id="session-4",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    skipped_bridge = OpenAICompactionBridge(message_count_threshold=5)
    skipped_capability = skipped_bridge.build_capability(session)
    skipped_context = ModelRequestContext(
        model=cast("Any", object.__new__(_FakeOpenAIResponsesModel)),
        messages=[
            ModelRequest(parts=[UserPromptPart(content="old")]),
            ModelRequest(parts=[UserPromptPart(content="new")]),
        ],
        model_settings=None,
        model_request_parameters=cast("Any", SimpleNamespace()),
    )

    unchanged_context = asyncio.run(
        skipped_capability.before_model_request(
            cast("Any", SimpleNamespace()),
            skipped_context,
        ),
    )

    assert unchanged_context is skipped_context
    assert skipped_bridge.drain_updates(session, Agent(TestModel())) is None

    failing_bridge = OpenAICompactionBridge(
        trigger=lambda _messages: True,
        instructions="Compact now.",
    )
    failing_capability = failing_bridge.build_capability(session)
    failing_context = ModelRequestContext(
        model=cast("Any", object.__new__(_FailingOpenAIResponsesModel)),
        messages=[
            ModelRequest(parts=[UserPromptPart(content="old")]),
            ModelRequest(parts=[UserPromptPart(content="new")]),
        ],
        model_settings=None,
        model_request_parameters=cast("Any", SimpleNamespace()),
    )

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            failing_capability.before_model_request(
                cast("Any", SimpleNamespace()),
                failing_context,
            ),
        )

    updates = failing_bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None
    assert len(updates) == 2
    start_update = updates[0]
    progress_update = updates[1]
    assert isinstance(start_update, ToolCallStart)
    assert isinstance(progress_update, ToolCallProgress)
    assert start_update.title == "Context Compaction"
    assert progress_update.tool_call_id == start_update.tool_call_id
    assert progress_update.status == "failed"
    assert progress_update.raw_output == "Provider: openai\nStatus: failed\nError: boom"
