from __future__ import annotations as _annotations

import asyncio

from .support import (
    AdapterConfig,
    Agent,
    ContentToolCallContent,
    FileEditToolCallContent,
    FileSystemProjectionMap,
    MemorySessionStore,
    Path,
    RecordingClient,
    TerminalToolCallContent,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    create_acp_agent,
    text_block,
)


def test_prompt_projects_filesystem_write_diff_content(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["write_file"], custom_output_text="write-complete"))

    @agent.tool_plain
    def write_file(path: str, content: str, old_text: str) -> str:
        return f"updated:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_write_tool="write_file")],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Update the file.")],
            session_id=session.session_id,
        )
    )

    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) == 2

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
    assert start_diff.old_text == "a"
    assert start_diff.new_text == "a"
    assert progress_diff.path == "a"
    assert progress_diff.old_text == "a"
    assert progress_diff.new_text == "a"


def test_prompt_projects_filesystem_read_diff_content(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["read_file"], custom_output_text="read-complete"))

    @agent.tool_plain
    def read_file(path: str) -> str:
        return f"contents:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_read_tool="read_file")],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Read the file.")],
            session_id=session.session_id,
        )
    )

    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) == 2

    tool_start = tool_updates[0]
    tool_progress = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert isinstance(tool_progress, ToolCallProgress)
    assert tool_start.content is None
    assert tool_progress.content is not None

    diff = tool_progress.content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.path == "a"
    assert diff.old_text == ""
    assert diff.new_text == "contents:a"


def test_projection_map_miss_keeps_generic_projection_fallback(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["read_file"], custom_output_text="fallback-complete"))

    @agent.tool_plain
    def read_file(path: str) -> str:
        return f"contents:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_write_tool="write_file")],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Read the file.")],
            session_id=session.session_id,
        )
    )

    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    tool_start = tool_updates[0]
    tool_progress = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert isinstance(tool_progress, ToolCallProgress)
    assert tool_start.kind == "read"
    assert tool_start.locations is not None
    assert tool_start.locations[0].path == "a"
    assert tool_start.content is None
    assert tool_progress.content is None
    assert tool_progress.raw_output == "contents:a"


def test_composite_projection_map_supports_read_and_write_rules(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["write_file"], custom_output_text="composite-complete"))

    @agent.tool_plain
    def write_file(path: str, content: str) -> str:
        return f"updated:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[
            FileSystemProjectionMap(default_read_tool="read_file"),
            FileSystemProjectionMap(default_write_tool="write_file"),
        ],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Update the file.")],
            session_id=session.session_id,
        )
    )

    tool_start = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    assert tool_start.content is not None
    diff = tool_start.content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.path == "a"
    assert diff.new_text == "a"


def test_prompt_projects_bash_command_preview_content(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["run_shell"], custom_output_text="bash-complete"))

    @agent.tool_plain
    def run_shell(script: str) -> dict[str, str | int]:
        return {
            "command": script,
            "pid": 123,
            "returncode": 0,
            "timed_out": 0,
            "stdout": "hello",
            "stderr": "",
        }

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[
            FileSystemProjectionMap(
                default_bash_tool="run_shell",
                command_arg="script",
            )
        ],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Run the shell command.")],
            session_id=session.session_id,
        )
    )

    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) == 2

    tool_start = tool_updates[0]
    tool_progress = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert isinstance(tool_progress, ToolCallProgress)
    assert tool_start.kind == "execute"
    assert tool_start.title == "Execute a"
    assert tool_progress.title == "Execute a"
    assert tool_start.content is not None
    assert tool_progress.content is not None

    start_content = tool_start.content[0]
    progress_content = tool_progress.content[0]
    assert isinstance(start_content, ContentToolCallContent)
    assert isinstance(progress_content, ContentToolCallContent)
    assert start_content.content.text == "```bash\na\n```"
    assert progress_content.content.text == "\n".join(
        (
            "Status: success",
            "",
            "```bash",
            "a",
            "```",
            "Exit code: 0",
            "",
            "Stdout:",
            "```text",
            "hello",
            "```",
        )
    )
    assert tool_progress.status == "completed"


def test_prompt_projects_bash_command_failure_sets_failed_status(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(call_tools=["run_shell"], custom_output_text="bash-complete"))

    @agent.tool_plain
    def run_shell(script: str) -> dict[str, str | int]:
        return {
            "command": script,
            "pid": 123,
            "returncode": 1,
            "timed_out": 0,
            "stdout": "",
            "stderr": "boom",
        }

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[
            FileSystemProjectionMap(
                default_bash_tool="run_shell",
                command_arg="script",
            )
        ],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Run the shell command.")],
            session_id=session.session_id,
        )
    )

    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_progress.status == "failed"
    assert tool_progress.content is not None
    progress_content = tool_progress.content[0]
    assert isinstance(progress_content, ContentToolCallContent)
    assert progress_content.content.text == "\n".join(
        (
            "Status: failed",
            "",
            "```bash",
            "a",
            "```",
            "Exit code: 1",
            "",
            "Stderr:",
            "```text",
            "boom",
            "```",
        )
    )


def test_prompt_projects_bash_terminal_reference_when_tool_returns_terminal_id(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(call_tools=["run_shell"], custom_output_text="bash-complete"))

    @agent.tool_plain
    def run_shell(script: str) -> dict[str, str]:
        del script
        return {"terminalId": "term-123"}

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[
            FileSystemProjectionMap(
                default_bash_tool="run_shell",
                command_arg="script",
            )
        ],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Run the shell command.")],
            session_id=session.session_id,
        )
    )

    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_progress.content is not None

    terminal_content = tool_progress.content[0]
    assert isinstance(terminal_content, TerminalToolCallContent)
    assert terminal_content.terminal_id == "term-123"
