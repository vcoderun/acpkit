# ACP Kit

ACP Kit is a monorepo providing adapter packages that turn Pydantic AI agents into ACP (Agent Communication Protocol) compatible servers. The toolkit consists of three main packages: `acpkit` (root CLI and target resolver), `pydantic-acp` (the core adapter that transforms `pydantic_ai.Agent` instances into ACP agents), and `codex-auth-helper` (authentication helper for Codex-backed models). This enables developers to expose their existing Pydantic AI agents through the ACP protocol without modifying agent code.

The framework supports comprehensive session management (create, load, fork, resume, close), capability bridges for extending ACP exposure, approval flows for gated tool execution, filesystem and terminal backends for host-client interactions, and projection maps for rendering tool operations as diffs. The adapter stays truthful about framework capabilities - it only exposes what the underlying Pydantic AI agent supports while providing hooks for customization through providers and bridges.

## CLI - Running Agents

The `acpkit` CLI resolves module targets and dispatches them to the appropriate adapter. It supports `module` or `module:attribute` format and automatically detects `pydantic_ai.Agent` instances.

```bash
# Run agent from module (selects last defined Agent in module)
acpkit run strong_agent

# Run specific agent attribute
acpkit run strong_agent:agent

# Add import roots for module resolution
acpkit run strong_agent:agent -p ./examples
acpkit run app.agents.demo:agent -p /absolute/path/to/agents
```

## run_acp - Starting an ACP Server

The `run_acp` function is the primary entry point for starting an ACP server with a Pydantic AI agent. It wraps the agent and runs the ACP protocol loop.

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

# Minimal static agent setup
agent = Agent(
    "openai:gpt-4",
    name="my-agent",
    system_prompt="You are a helpful assistant.",
)

@agent.tool_plain
def get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Weather in {city}: Sunny, 72F"

# Start the ACP server
run_acp(agent=agent)
```

## create_acp_agent - Creating ACP Agent Without Running

The `create_acp_agent` function creates an ACP-compatible agent object that can be run separately or composed with other services.

```python
from acp import run_agent
from pydantic_ai import Agent
from pydantic_acp import create_acp_agent, AdapterConfig, MemorySessionStore

agent = Agent("openai:gpt-4", name="composable-agent")

# Create ACP agent without starting server
acp_agent = create_acp_agent(
    agent=agent,
    config=AdapterConfig(
        agent_name="my-service",
        agent_title="My Service Agent",
        session_store=MemorySessionStore(),
    ),
)

# Run manually or integrate with other async services
import asyncio
asyncio.run(run_agent(acp_agent))
```

## AdapterConfig - Configuring the Adapter

The `AdapterConfig` dataclass provides comprehensive configuration for the adapter including session storage, model selection, approval flows, and capability bridges.

```python
from pathlib import Path
from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    AdapterModel,
    FileSessionStore,
    NativeApprovalBridge,
    FileSystemProjectionMap,
    run_acp,
)

agent = Agent("openai:gpt-4", name="configured-agent")

config = AdapterConfig(
    agent_name="my-agent",
    agent_title="My Agent Title",
    agent_version="1.0.0",
    # Enable model selection in ACP client
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id="fast",
            name="Fast Model",
            description="Low-latency responses",
            override="openai:gpt-3.5-turbo",
        ),
        AdapterModel(
            model_id="smart",
            name="Smart Model",
            description="Higher quality responses",
            override="openai:gpt-4",
        ),
    ],
    # File-backed session persistence
    session_store=FileSessionStore(base_dir=Path(".acp-sessions")),
    # Enable approval flow with persistent choices
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
    # Replay message history when loading sessions
    replay_history_on_load=True,
)

run_acp(agent=agent, config=config)
```

## Agent Factory - Session-Aware Agent Creation

Use an agent factory when the agent needs access to session context at construction time. The factory receives `AcpSessionContext` with session metadata, working directory, and configuration values.

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AdapterConfig, run_acp

def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    """Factory receives session context for dynamic agent construction."""
    # Access session properties
    workspace_name = session.cwd.name
    verbose_mode = session.config_values.get("verbose", False)

    system_prompt = f"Working in {workspace_name}."
    if verbose_mode:
        system_prompt += " Provide detailed explanations."

    return Agent(
        "openai:gpt-4",
        name=f"agent-{workspace_name}",
        system_prompt=system_prompt,
    )

run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(agent_name="factory-agent"),
)
```

## Session Stores - Persistence Options

ACP Kit provides two session store implementations: `MemorySessionStore` for ephemeral sessions and `FileSessionStore` for persistent sessions across restarts.

```python
from pathlib import Path
from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    FileSessionStore,
    MemorySessionStore,
    run_acp,
)

agent = Agent("openai:gpt-4", name="persistent-agent")

# In-memory sessions (default, lost on restart)
memory_config = AdapterConfig(
    session_store=MemorySessionStore(),
)

# File-backed sessions (persisted to disk)
file_config = AdapterConfig(
    session_store=FileSessionStore(root=Path(".sessions")),
)

# FileSessionStore supports full session lifecycle
# - save(session) - persist session state
# - get(session_id) - load session
# - list_sessions() - list all sessions sorted by updated_at
# - fork(session_id, new_session_id, cwd) - create branch
# - delete(session_id) - remove session

run_acp(agent=agent, config=file_config)
```

## Approval Flow - Gated Tool Execution

Tools can require approval before execution using Pydantic AI's native approval mechanism. The adapter bridges this to ACP permission requests.

```python
from pydantic_ai import Agent
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.tools import RunContext
from pydantic_acp import AdapterConfig, NativeApprovalBridge, run_acp

agent = Agent("openai:gpt-4", name="approval-example")

@agent.tool
def delete_file(ctx: RunContext[None], path: str) -> str:
    """Delete a file - requires approval."""
    if not ctx.tool_call_approved:
        raise ApprovalRequired()
    # Tool execution continues only after user approval
    return f"Deleted: {path}"

# Alternative: use requires_approval parameter
@agent.tool_plain(requires_approval=True)
def write_config(path: str, content: str) -> str:
    """Write configuration file - always requires approval."""
    return f"Wrote {len(content)} bytes to {path}"

config = AdapterConfig(
    approval_bridge=NativeApprovalBridge(
        enable_persistent_choices=True,  # Remember approval decisions
    ),
)

run_acp(agent=agent, config=config)
```

## FileSystemProjectionMap - Diff Rendering

The `FileSystemProjectionMap` enables rich diff visualization for file operations, showing read content and write changes in the ACP client.

```python
from pydantic_ai import Agent
from pydantic_acp import FileSystemProjectionMap, run_acp

agent = Agent("openai:gpt-4", name="diff-agent")

@agent.tool_plain
def read_file(path: str) -> str:
    """Read file contents."""
    with open(path) as f:
        return f.read()

@agent.tool_plain(requires_approval=True)
def write_file(path: str, content: str) -> str:
    """Write file with content diff preview."""
    with open(path, "w") as f:
        f.write(content)
    return f"Wrote {path}"

@agent.tool_plain
def run_command(command: str) -> str:
    """Execute bash command with preview."""
    import subprocess
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return result.stdout or result.stderr

run_acp(
    agent=agent,
    projection_maps=(
        FileSystemProjectionMap(
            # Map tool names to projection types
            default_read_tool="read_file",
            default_write_tool="write_file",
            default_bash_tool="run_command",
            # Or use sets for multiple tools
            read_tool_names=frozenset({"read_file", "cat_file"}),
            write_tool_names=frozenset({"write_file", "edit_file"}),
            bash_tool_names=frozenset({"run_command", "execute"}),
        ),
    ),
)
```

## HookProjectionMap - Hook Event Rendering

The `HookProjectionMap` controls how Pydantic AI hook events are rendered in ACP updates. It can customize labels, visibility, and formatting of hook lifecycle events.

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks
from pydantic_acp import HookProjectionMap, run_acp

# Define hooks capability
hooks = Hooks[None]()

@hooks.on.before_model_request
async def log_request(ctx, request_context):
    print(f"Model request starting...")
    return request_context

@hooks.on.after_model_request
async def log_response(ctx, *, request_context, response):
    print(f"Model response received")
    return response

@hooks.on.before_tool_execute(tools=["search"])
async def log_tool(ctx, *, call, tool_def, args):
    print(f"Tool {tool_def.name} executing with {args}")
    return args

agent = Agent(
    "openai:gpt-4",
    name="hooks-agent",
    capabilities=[hooks],
)

@agent.tool_plain
def search(query: str) -> str:
    return f"Results for: {query}"

run_acp(
    agent=agent,
    projection_maps=(
        HookProjectionMap(
            # Hide specific events from ACP updates
            hidden_event_ids=frozenset({"after_model_request"}),
            # Custom labels for events
            event_labels={
                "before_model_request": "Preparing Request",
                "before_tool_execute": "Starting Tool",
                "after_tool_execute": "Tool Complete",
            },
        ),
    ),
)
```

## Capability Bridges - Extending ACP Exposure

Capability bridges enrich ACP exposure by providing hooks, history processors, tool preparation modes, and MCP metadata without coupling the adapter core to specific product models.

```python
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AgentBridgeBuilder,
    HistoryProcessorBridge,
    HookBridge,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    PrepareToolsBridge,
    PrepareToolsMode,
    run_acp,
)

# Define mode-aware tool filtering
def chat_tools(ctx: RunContext[None], tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """Hide MCP tools in chat mode."""
    return [t for t in tools if not t.name.startswith("mcp.")]

def review_tools(ctx: RunContext[None], tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """Show all tools in review mode."""
    return tools

# Define history trimming
def trim_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Keep only recent messages."""
    return messages[-4:]

# Build bridges
bridges = [
    HookBridge(),  # Hook lifecycle events
    HistoryProcessorBridge(),  # History processing projection
    PrepareToolsBridge(
        default_mode_id="chat",
        modes=[
            PrepareToolsMode(id="chat", name="Chat",
                           description="Conversational mode", prepare_func=chat_tools),
            PrepareToolsMode(id="review", name="Review",
                           description="Full tool access", prepare_func=review_tools),
        ],
    ),
    McpBridge(
        servers=[
            McpServerDefinition(
                server_id="repo",
                name="Repository Tools",
                transport="http",
                tool_prefix="mcp.repo.",
            ),
        ],
        tools=[
            McpToolDefinition(tool_name="mcp.repo.search", server_id="repo", kind="search"),
            McpToolDefinition(tool_name="mcp.repo.read", server_id="repo", kind="read"),
        ],
    ),
]

def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    builder = AgentBridgeBuilder(session=session, capability_bridges=bridges)
    contributions = builder.build(plain_history_processors=[trim_history])

    agent = Agent(
        "openai:gpt-4",
        name="bridge-agent",
        capabilities=contributions.capabilities,
        history_processors=contributions.history_processors,
    )

    @agent.tool_plain(name="mcp.repo.search")
    def search_repo(query: str) -> str:
        return f"Found matches for: {query}"

    return agent

run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(capability_bridges=bridges),
)
```

## Session Providers - Host-Owned State

Providers let the host own session state (models, modes, config options, plans) while the adapter exposes them through ACP. This enables product-specific customization without modifying the adapter core.

```python
from dataclasses import dataclass
from acp.schema import SessionMode, SessionConfigOptionBoolean, PlanEntry
from pydantic_ai import Agent
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    ConfigOption,
    ModelSelectionState,
    ModeState,
    run_acp,
)

@dataclass
class ModelsProvider:
    def get_model_state(self, session: AcpSessionContext, agent: Agent) -> ModelSelectionState:
        current = session.config_values.get("model_id", "standard")
        return ModelSelectionState(
            available_models=[
                AdapterModel(model_id="fast", name="Fast",
                           description="Quick responses", override="gpt-3.5-turbo"),
                AdapterModel(model_id="standard", name="Standard",
                           description="Balanced", override="gpt-4"),
            ],
            current_model_id=str(current),
        )

    def set_model(self, session: AcpSessionContext, agent: Agent, model_id: str) -> ModelSelectionState:
        session.config_values["model_id"] = model_id
        return self.get_model_state(session, agent)

@dataclass
class ModesProvider:
    def get_mode_state(self, session: AcpSessionContext, agent: Agent) -> ModeState:
        current = session.config_values.get("mode_id", "chat")
        return ModeState(
            modes=[
                SessionMode(id="chat", name="Chat", description="Conversational mode"),
                SessionMode(id="code", name="Code", description="Code generation mode"),
            ],
            current_mode_id=str(current),
        )

    def set_mode(self, session: AcpSessionContext, agent: Agent, mode_id: str) -> ModeState:
        session.config_values["mode_id"] = mode_id
        return self.get_mode_state(session, agent)

@dataclass
class ConfigProvider:
    def get_config_options(self, session: AcpSessionContext, agent: Agent) -> list[ConfigOption]:
        verbose = session.config_values.get("verbose", False)
        return [
            SessionConfigOptionBoolean(
                id="verbose", name="Verbose Mode", category="output",
                description="Show detailed responses", type="boolean",
                current_value=bool(verbose),
            ),
        ]

    def set_config_option(self, session: AcpSessionContext, agent: Agent,
                         config_id: str, value: str | bool) -> list[ConfigOption] | None:
        if config_id == "verbose" and isinstance(value, bool):
            session.config_values["verbose"] = value
            return self.get_config_options(session, agent)
        return None

@dataclass
class PlanProvider:
    def get_plan(self, session: AcpSessionContext, agent: Agent) -> list[PlanEntry]:
        mode = session.config_values.get("mode_id", "chat")
        return [
            PlanEntry(content=f"Current mode: {mode}", priority="high", status="in_progress"),
        ]

agent = Agent("openai:gpt-4", name="provider-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        models_provider=ModelsProvider(),
        modes_provider=ModesProvider(),
        config_options_provider=ConfigProvider(),
        plan_provider=PlanProvider(),
    ),
)
```

## Host Backends - Client Filesystem and Terminal

The `ClientHostContext` provides session-scoped access to ACP client-backed filesystem and terminal operations, enabling tools to interact with the user's local environment.

```python
from acp.interfaces import Client as AcpClient
from pydantic_ai import Agent
from pydantic_ai.tools import RunContext
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    ClientHostContext,
    create_acp_agent,
)

def build_agent(client: AcpClient, session: AcpSessionContext) -> Agent[None, str]:
    # Create host context for client-backed operations
    host = ClientHostContext.from_session(client=client, session=session)

    agent = Agent("openai:gpt-4", name="host-agent")

    @agent.tool
    async def read_user_file(ctx: RunContext[None], path: str) -> str:
        """Read a file from user's filesystem via ACP client."""
        response = await host.filesystem.read_text_file(path)
        return response.content

    @agent.tool
    async def write_user_file(ctx: RunContext[None], path: str, content: str) -> str:
        """Write a file to user's filesystem via ACP client."""
        await host.filesystem.write_text_file(path, content)
        return f"Wrote {len(content)} bytes to {path}"

    @agent.tool
    async def run_user_command(ctx: RunContext[None], command: str) -> str:
        """Execute a command in user's terminal via ACP client."""
        terminal = await host.terminal.create_terminal(
            "bash",
            args=["-c", command],
            cwd=str(session.cwd),
            output_byte_limit=4096,
        )
        await host.terminal.wait_for_terminal_exit(terminal.terminal_id)
        output = await host.terminal.terminal_output(terminal.terminal_id)
        await host.terminal.release_terminal(terminal.terminal_id)
        return output.output

    return agent

# Note: Client binding requires custom wrapper to capture on_connect
# See examples/pydantic/strong_agent.py for full implementation
```

## Codex Auth Helper - Codex-Backed Models

The `codex-auth-helper` package enables using Codex authentication with Pydantic AI models. It handles token refresh and provides a drop-in OpenAI Responses model.

```python
from pydantic_ai import Agent
from codex_auth_helper import (
    create_codex_responses_model,
    CodexAuthConfig,
    CodexTokenManager,
)

# Simple usage - uses default Codex auth file location
agent = Agent(
    create_codex_responses_model("gpt-4"),
    name="codex-agent",
)

# Custom configuration
config = CodexAuthConfig(
    auth_file_path="~/.codex/auth.json",  # Custom auth file location
)
model = create_codex_responses_model(
    "gpt-4",
    config=config,
)

agent_with_config = Agent(model, name="configured-codex-agent")

# Run with ACP
from pydantic_acp import run_acp
run_acp(agent=agent)
```

## Complete Example - Full-Featured Agent

A comprehensive example combining factories, providers, bridges, approvals, and host backends.

```python
from dataclasses import dataclass
from pathlib import Path
from acp.schema import SessionMode
from pydantic_ai import Agent
from pydantic_ai.tools import RunContext
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    AgentBridgeBuilder,
    FileSessionStore,
    FileSystemProjectionMap,
    HookBridge,
    HistoryProcessorBridge,
    ModelSelectionState,
    ModeState,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    run_acp,
)

# Providers for session state
@dataclass
class DemoModelsProvider:
    def get_model_state(self, session: AcpSessionContext, agent: Agent) -> ModelSelectionState:
        return ModelSelectionState(
            available_models=[
                AdapterModel(model_id="fast", name="Fast", override="gpt-3.5-turbo"),
                AdapterModel(model_id="smart", name="Smart", override="gpt-4"),
            ],
            current_model_id=str(session.config_values.get("model", "smart")),
        )

    def set_model(self, session: AcpSessionContext, agent: Agent, model_id: str):
        session.config_values["model"] = model_id
        return self.get_model_state(session, agent)

@dataclass
class DemoModesProvider:
    def get_mode_state(self, session: AcpSessionContext, agent: Agent) -> ModeState:
        return ModeState(
            modes=[
                SessionMode(id="chat", name="Chat", description="Conversational"),
                SessionMode(id="code", name="Code", description="Code-focused"),
            ],
            current_mode_id=str(session.config_values.get("mode", "chat")),
        )

    def set_mode(self, session: AcpSessionContext, agent: Agent, mode_id: str):
        session.config_values["mode"] = mode_id
        return self.get_mode_state(session, agent)

# Tool filtering by mode
def chat_tools(ctx, tools):
    return [t for t in tools if not t.name.startswith("code_")]

def code_tools(ctx, tools):
    return tools

# Bridges
bridges = [
    HookBridge(),
    HistoryProcessorBridge(),
    PrepareToolsBridge(
        default_mode_id="chat",
        modes=[
            PrepareToolsMode(id="chat", name="Chat", prepare_func=chat_tools),
            PrepareToolsMode(id="code", name="Code", prepare_func=code_tools),
        ],
    ),
]

def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    builder = AgentBridgeBuilder(session=session, capability_bridges=bridges)
    contributions = builder.build()

    agent = Agent(
        "openai:gpt-4",
        name=f"demo-{session.cwd.name}",
        capabilities=contributions.capabilities,
        history_processors=contributions.history_processors,
    )

    @agent.tool_plain
    def search_files(query: str) -> str:
        return f"Found files matching: {query}"

    @agent.tool_plain
    def read_file(path: str) -> str:
        return Path(path).read_text()

    @agent.tool_plain(requires_approval=True)
    def write_file(path: str, content: str) -> str:
        Path(path).write_text(content)
        return f"Wrote {path}"

    @agent.tool_plain(name="code_analyze")
    def analyze_code(path: str) -> str:
        """Only visible in code mode."""
        return f"Analysis of {path}: OK"

    return agent

run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(
        agent_name="full-demo",
        session_store=FileSessionStore(Path(".sessions")),
        approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
        capability_bridges=bridges,
        models_provider=DemoModelsProvider(),
        modes_provider=DemoModesProvider(),
    ),
    projection_maps=(
        FileSystemProjectionMap(
            default_read_tool="read_file",
            default_write_tool="write_file",
        ),
    ),
)
```

## Summary

ACP Kit enables seamless integration of Pydantic AI agents with the Agent Communication Protocol. The primary use cases include: exposing existing agents through ACP without code changes using `run_acp()`, building session-aware agents with factories that receive workspace context, implementing approval flows for sensitive operations, and rendering rich diff previews for file operations. The toolkit supports both simple single-agent setups and complex multi-bridge configurations with custom providers.

Integration patterns range from minimal setups (just `run_acp(agent=agent)`) to sophisticated architectures with capability bridges, host backends, and session providers. The adapter respects the truthfulness principle - it only exposes capabilities the underlying Pydantic AI framework actually supports. For production deployments, use `FileSessionStore` for persistence, configure `NativeApprovalBridge` for gated operations, and implement custom providers when session state needs to be owned by the host application layer.
