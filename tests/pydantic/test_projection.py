from __future__ import annotations as _annotations

import asyncio
from types import SimpleNamespace

import pytest
from acp.schema import ToolCallLocation
from pydantic_acp.projection import (
    BuiltinToolProjectionMap,
    DefaultToolClassifier,
    ToolProjection,
    WebToolProjectionMap,
    _append_string_list_line,
    _bash_progress_content,
    _compaction_raw_input,
    _compaction_tool_call_id,
    _extract_search_results,
    _format_compaction_progress,
    _format_image_generation_progress,
    _format_image_generation_start,
    _format_mcp_progress,
    _format_mcp_start,
    _format_mcp_title,
    _format_web_fetch_progress,
    _format_web_fetch_start,
    _format_web_search_progress,
    _format_web_search_start,
    _is_binary_like_content,
    _json_preview,
    _preserve_file_diff_content,
    _read_existing_text,
    _web_fetch_url,
    _web_search_query,
    build_compaction_updates,
    build_tool_progress_update,
    build_tool_start_update,
    build_tool_updates,
)
from pydantic_acp.serialization import DefaultOutputSerializer
from pydantic_ai import (
    BuiltinToolCallPart,
    BuiltinToolReturnPart,
    CompactionPart,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
)

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


def test_builtin_web_search_projection_renders_query_and_results() -> None:
    projection_map = WebToolProjectionMap()
    classifier = DefaultToolClassifier()
    serializer = DefaultOutputSerializer()
    tool_call = BuiltinToolCallPart(
        tool_name="web_search",
        args={
            "query": "acpkit",
            "allowed_domains": ["example.com"],
            "search_context_size": "high",
        },
        tool_call_id="search-1",
    )

    start_update = build_tool_start_update(
        tool_call,
        classifier=classifier,
        projection_map=projection_map,
    )
    progress_update = build_tool_progress_update(
        BuiltinToolReturnPart(
            tool_name="web_search",
            tool_call_id="search-1",
            content=[
                {
                    "title": "ACP Kit",
                    "url": "https://example.com/acpkit",
                    "snippet": "Adapter toolkit for truthful ACP servers.",
                }
            ],
        ),
        classifier=classifier,
        known_start=start_update,
        projection_map=projection_map,
        serializer=serializer,
    )

    assert start_update.kind == "search"
    assert start_update.title == "Search web for acpkit"
    assert start_update.content is not None
    start_content = start_update.content[0]
    assert isinstance(start_content, ContentToolCallContent)
    assert "Query: acpkit" in start_content.content.text
    assert "Allowed domains: example.com" in start_content.content.text
    assert progress_update.content is not None
    progress_content = progress_update.content[0]
    assert isinstance(progress_content, ContentToolCallContent)
    assert "1. ACP Kit" in progress_content.content.text
    assert "https://example.com/acpkit" in progress_content.content.text


def test_builtin_web_fetch_projection_renders_url_and_preview() -> None:
    projection_map = WebToolProjectionMap()
    classifier = DefaultToolClassifier()
    serializer = DefaultOutputSerializer()
    tool_call = BuiltinToolCallPart(
        tool_name="web_fetch",
        args={"url": "https://example.com/docs"},
        tool_call_id="fetch-1",
    )

    start_update = build_tool_start_update(
        tool_call,
        classifier=classifier,
        projection_map=projection_map,
    )
    progress_update = build_tool_progress_update(
        BuiltinToolReturnPart(
            tool_name="web_fetch",
            tool_call_id="fetch-1",
            content={
                "url": "https://example.com/docs",
                "title": "Example Docs",
                "content": "hello from the fetched page",
            },
        ),
        classifier=classifier,
        known_start=start_update,
        projection_map=projection_map,
        serializer=serializer,
    )

    assert start_update.kind == "fetch"
    assert start_update.title == "Fetch https://example.com/docs"
    assert start_update.content is not None
    start_content = start_update.content[0]
    assert isinstance(start_content, ContentToolCallContent)
    assert "URL: https://example.com/docs" in start_content.content.text
    assert progress_update.content is not None
    progress_content = progress_update.content[0]
    assert isinstance(progress_content, ContentToolCallContent)
    assert "Title: Example Docs" in progress_content.content.text
    assert "hello from the fetched page" in progress_content.content.text


def test_web_projection_map_handles_binary_fetch_and_search_fallback_output() -> None:
    projection_map = WebToolProjectionMap()

    binary_projection = projection_map.project_progress(
        "web_fetch",
        raw_output=SimpleNamespace(media_type="image/png", data=b"png-bytes"),
        serialized_output="ignored",
        status="completed",
    )
    search_projection = projection_map.project_progress(
        "web_search",
        raw_output={"unexpected": "shape"},
        serialized_output="fallback output",
        status="completed",
    )

    assert binary_projection is not None
    assert binary_projection.content is not None
    binary_content = binary_projection.content[0]
    assert isinstance(binary_content, ContentToolCallContent)
    assert binary_content.content.text == "Fetched binary content (image/png)."
    assert search_projection is not None
    assert search_projection.content is not None
    search_content = search_projection.content[0]
    assert isinstance(search_content, ContentToolCallContent)
    assert search_content.content.text == "fallback output"


def test_build_tool_updates_supports_builtin_tool_call_and_return_parts() -> None:
    updates = build_tool_updates(
        [
            ModelResponse(
                parts=[
                    BuiltinToolCallPart(
                        tool_name="web_search",
                        args={"query": "acpkit"},
                        tool_call_id="search-1",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    BuiltinToolReturnPart(
                        tool_name="web_search",
                        tool_call_id="search-1",
                        content={"results": [{"title": "ACP Kit", "href": "https://example.com"}]},
                    )
                ]
            ),
        ],
        classifier=DefaultToolClassifier(),
        projection_map=WebToolProjectionMap(),
        serializer=DefaultOutputSerializer(),
    )

    assert len(updates) == 2
    assert isinstance(updates[0], ToolCallStart)
    assert isinstance(updates[1], ToolCallProgress)
    assert updates[0].kind == "search"
    assert updates[1].content is not None
    progress_content = updates[1].content[0]
    assert isinstance(progress_content, ContentToolCallContent)
    assert "ACP Kit" in progress_content.content.text


def test_builtin_image_generation_projection_renders_prompt_and_result_summary() -> None:
    projection_map = BuiltinToolProjectionMap()
    classifier = DefaultToolClassifier()
    serializer = DefaultOutputSerializer()
    tool_call = BuiltinToolCallPart(
        tool_name="image_generation",
        args={
            "prompt": "a kiwi bird in a raincoat",
            "quality": "high",
            "size": "1024x1024",
        },
        tool_call_id="img-1",
    )

    start_update = build_tool_start_update(
        tool_call,
        classifier=classifier,
        projection_map=projection_map,
    )
    progress_update = build_tool_progress_update(
        BuiltinToolReturnPart(
            tool_name="image_generation",
            tool_call_id="img-1",
            content={
                "status": "completed",
                "revised_prompt": "a cheerful kiwi bird in a yellow raincoat",
                "quality": "high",
                "size": "1024x1024",
            },
        ),
        classifier=classifier,
        known_start=start_update,
        projection_map=projection_map,
        serializer=serializer,
    )

    assert start_update.title == "Generate image for a kiwi bird in a raincoat"
    assert start_update.content is not None
    start_content = start_update.content[0]
    assert isinstance(start_content, ContentToolCallContent)
    assert "Prompt: a kiwi bird in a raincoat" in start_content.content.text
    assert "Quality: high" in start_content.content.text
    assert progress_update.content is not None
    progress_content = progress_update.content[0]
    assert isinstance(progress_content, ContentToolCallContent)
    assert "Status: completed" in progress_content.content.text
    assert (
        "Revised prompt: a cheerful kiwi bird in a yellow raincoat" in progress_content.content.text
    )


def test_builtin_mcp_projection_renders_start_and_progress_for_tool_calls() -> None:
    projection_map = BuiltinToolProjectionMap()
    classifier = DefaultToolClassifier()
    serializer = DefaultOutputSerializer()
    tool_call = BuiltinToolCallPart(
        tool_name="mcp_server:repo",
        args={
            "action": "call_tool",
            "tool_name": "search",
            "tool_args": {"query": "acpkit"},
        },
        tool_call_id="mcp-1",
    )

    start_update = build_tool_start_update(
        tool_call,
        classifier=classifier,
        projection_map=projection_map,
    )
    progress_update = build_tool_progress_update(
        BuiltinToolReturnPart(
            tool_name="mcp_server:repo",
            tool_call_id="mcp-1",
            content={
                "output": [{"path": "README.md"}],
                "error": None,
            },
        ),
        classifier=classifier,
        known_start=start_update,
        projection_map=projection_map,
        serializer=serializer,
    )

    assert start_update.title == "Call search via MCP repo"
    assert start_update.content is not None
    start_content = start_update.content[0]
    assert isinstance(start_content, ContentToolCallContent)
    assert "Server: repo" in start_content.content.text
    assert "Action: call_tool" in start_content.content.text
    assert "Tool: search" in start_content.content.text
    assert progress_update.content is not None
    progress_content = progress_update.content[0]
    assert isinstance(progress_content, ContentToolCallContent)
    assert "Output:" in progress_content.content.text
    assert "README.md" in progress_content.content.text


def test_builtin_projection_map_delegates_web_tools_and_handles_mcp_list_tools() -> None:
    projection_map = BuiltinToolProjectionMap()
    search_projection = projection_map.project_start(
        "web_search",
        raw_input={"query": "acpkit"},
    )
    mcp_projection = projection_map.project_progress(
        "mcp_server:repo",
        raw_output={
            "tools": [{"name": "search"}, {"name": "read_file"}],
            "error": None,
        },
        serialized_output="ignored",
        status="completed",
    )

    assert search_projection is not None
    assert search_projection.title == "Search web for acpkit"
    assert mcp_projection is not None
    assert mcp_projection.content is not None
    mcp_content = mcp_projection.content[0]
    assert isinstance(mcp_content, ContentToolCallContent)
    assert "Tools listed: 2" in mcp_content.content.text
    assert "Preview: search, read_file" in mcp_content.content.text


def test_build_compaction_updates_renders_anthropic_summary() -> None:
    updates = build_compaction_updates(
        [
            ModelResponse(
                parts=[
                    CompactionPart(
                        content="Summary of prior conversation.",
                        provider_name="anthropic",
                    )
                ]
            )
        ]
    )

    assert len(updates) == 2
    start_update = updates[0]
    progress_update = updates[1]
    assert isinstance(start_update, ToolCallStart)
    assert isinstance(progress_update, ToolCallProgress)
    assert start_update.title == "Context Compaction"
    assert start_update.raw_input == {"provider": "anthropic"}
    assert progress_update.raw_output == "\n".join(
        (
            "Provider: anthropic",
            "Status: history compacted",
            "",
            "Summary:",
            "Summary of prior conversation.",
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


def test_projection_helper_edge_paths_and_fallbacks() -> None:
    file_diff = FileEditToolCallContent(type="diff", path="a.py", old_text="old", new_text="new")
    text_content = ContentToolCallContent(type="content", content=text_block("note"))
    known_start = ToolCallStart(
        session_update="tool_call",
        tool_call_id="call-1",
        title="Edit a.py",
        kind="edit",
        status="in_progress",
        content=[file_diff],
        locations=[ToolCallLocation(path="a.py")],
    )

    assert _web_search_query({"other": "value"}) is None
    assert _web_fetch_url({"other": "value"}) is None
    assert _format_web_search_start({"user_location": {"city": "", "country": None}}) == (
        "Searching the web."
    )
    assert _format_web_fetch_start({"max_content_tokens": 128, "enable_citations": False}) == (
        "Max content tokens: 128\nCitations enabled: no"
    )

    lines: list[str] = []
    _append_string_list_line(lines, "Allowed domains", ["", 1, None])
    assert lines == []

    assert _extract_search_results("invalid") is None
    assert _extract_search_results({"results": [{"title": "ACP Kit"}]}) == [{"title": "ACP Kit"}]
    assert _format_web_fetch_progress({}, "fallback fetch output") == "fallback fetch output"
    assert _format_image_generation_progress(None, "fallback image output") == (
        "fallback image output"
    )
    assert _format_mcp_title("mcp_server:repo", {"action": "list_tools"}) == (
        "List tools from MCP repo"
    )
    assert _format_mcp_title("mcp_server:repo", {"action": "noop"}) == "Use MCP repo"

    mcp_progress = _format_mcp_progress(
        {"error": "boom", "tools": [1, {"name": "search"}], "output": []},
        "unused",
    )
    assert "Error: boom" in mcp_progress
    preview_mcp_progress = _format_mcp_progress(
        {"tools": [{"name": "search"}, {"name": "read_file"}]},
        "unused",
    )
    assert "Tools listed: 2" in preview_mcp_progress
    assert "Preview: search, read_file" in preview_mcp_progress
    assert _format_mcp_progress({}, "fallback mcp output") == "fallback mcp output"
    assert _json_preview({"problem": object()}).startswith("{'problem':")
    assert _is_binary_like_content(None) is False

    assert _preserve_file_diff_content(known_start=None, projection=None) is None
    assert (
        _preserve_file_diff_content(
            known_start=known_start,
            projection=ToolProjection(content=[]),
        )
        == []
    )
    assert _preserve_file_diff_content(
        known_start=known_start,
        projection=ToolProjection(content=[text_content]),
    ) == [text_content]
    mismatched_known_start = ToolCallStart(
        session_update="tool_call",
        tool_call_id="call-2",
        title="Edit a.py",
        kind="edit",
        status="in_progress",
        content=[file_diff, file_diff],
    )
    assert _preserve_file_diff_content(
        known_start=mismatched_known_start,
        projection=ToolProjection(content=[file_diff]),
    ) == [file_diff]


def test_projection_maps_cover_unmatched_and_incomplete_paths() -> None:
    web_projection = WebToolProjectionMap()
    builtin_projection = BuiltinToolProjectionMap()

    assert web_projection.project_start("web_search", raw_input="invalid") is None
    assert (
        web_projection.project_start("web_search", raw_input={"allowed_domains": ["example.com"]})
        is None
    )
    assert web_projection.project_start("web_fetch", raw_input={"query": "missing-url"}) is None
    assert (
        web_projection.project_progress(
            "web_search",
            raw_output=[{"title": "ACP Kit"}],
            serialized_output="ignored",
            status="in_progress",
        )
        is None
    )

    delegated_web_projection = builtin_projection.project_progress(
        "web_search",
        raw_output=[{"title": "ACP Kit"}],
        serialized_output="ignored",
        status="completed",
    )
    assert delegated_web_projection is not None
    assert builtin_projection.project_start("image_generation", raw_input="invalid") is None
    assert (
        builtin_projection.project_start("unknown_builtin", raw_input={"prompt": "hello"}) is None
    )
    assert (
        builtin_projection.project_progress(
            "image_generation",
            raw_output={"status": "completed"},
            serialized_output="ignored",
            status="in_progress",
        )
        is None
    )
    assert (
        builtin_projection.project_progress(
            "unknown_builtin",
            raw_output={"status": "completed"},
            serialized_output="ignored",
            status="completed",
        )
        is None
    )


def test_projection_helper_edges_cover_binary_command_and_mcp_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_projection = ToolProjection(content=[])
    known_start = ToolCallStart(
        session_update="tool_call",
        tool_call_id="diff-1",
        title="Edit notes.md",
        kind="edit",
        status="in_progress",
        content=[],
    )

    assert _preserve_file_diff_content(known_start=known_start, projection=empty_projection) == []
    assert _read_existing_text("notes.md", cwd=None) == ""

    readable_path = tmp_path / "notes.md"
    readable_path.write_text("notes", encoding="utf-8")

    def raise_oserror(self: Path, encoding: str = "utf-8", errors: str = "replace") -> str:
        del encoding, errors
        raise OSError("blocked")

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    assert _read_existing_text(str(readable_path), cwd=tmp_path) == ""

    bash_content = _bash_progress_content(
        raw_input={"command": "echo hello"},
        raw_output={"stdout": "", "stderr": ""},
        serialized_output="ignored",
    )
    assert isinstance(bash_content[0], ContentToolCallContent)
    assert bash_content[0].content.text == "\n".join(
        (
            "Status: success",
            "",
            "```bash",
            "echo hello",
            "```",
        )
    )

    assert "User location: Istanbul, TR, Europe/Istanbul" in _format_web_search_start(
        {
            "query": "acpkit",
            "user_location": {
                "city": "Istanbul",
                "country": "TR",
                "timezone": "Europe/Istanbul",
            },
        }
    )
    assert (
        _format_web_search_progress(
            [{"title": "ACP Kit", "snippet": "adapter toolkit"}],
            "ignored",
        )
        == "1. ACP Kit\nadapter toolkit"
    )
    assert _format_web_fetch_progress(
        SimpleNamespace(data=b"hello", media_type=""),
        "ignored",
    ) == ("Fetched binary content.")
    assert _format_image_generation_start({}) == "Generating image."
    assert _format_mcp_start("mcp_server:repo", {"action": "list_tools"}) == "\n".join(
        ("Server: repo", "Action: list_tools")
    )
    assert _format_mcp_progress({"tools": [{"id": "search"}]}, "fallback") == "Tools listed: 1"
    assert _format_mcp_progress("invalid", "fallback") == "fallback"


def test_compaction_helpers_cover_skips_collisions_and_payload_variants() -> None:
    known_start = ToolCallStart(
        session_update="tool_call",
        tool_call_id="compaction:anthropic:1",
        title="Context Compaction",
        kind="execute",
        status="in_progress",
    )
    collision_part = CompactionPart(content=None, provider_name="anthropic")
    provider_details_part = CompactionPart(
        id="cmp-1",
        content=None,
        provider_name="anthropic",
        provider_details={"raw": "payload"},
    )
    completed_part = CompactionPart(
        id="cmp-2",
        content=None,
        provider_name="anthropic",
        provider_details=None,
    )

    assert (
        build_compaction_updates(
            [ModelResponse(parts=[CompactionPart(content="skip", provider_name="openai")])],
            skip_providers=frozenset({"openai"}),
        )
        == []
    )
    assert (
        _compaction_tool_call_id(
            collision_part,
            provider_name="anthropic",
            known_starts={"compaction:anthropic:1": known_start},
            created_count=0,
        )
        == "compaction:anthropic:2"
    )
    assert (
        _compaction_tool_call_id(
            provider_details_part,
            provider_name="anthropic",
            known_starts={},
            created_count=0,
        )
        == "compaction:anthropic:cmp-1"
    )
    assert _compaction_raw_input(provider_details_part, provider_name="anthropic") == {
        "provider": "anthropic",
        "compaction_id": "cmp-1",
    }
    assert _format_compaction_progress(
        provider_details_part, provider_name="anthropic"
    ) == "\n".join(
        (
            "Provider: anthropic",
            "Status: history compacted",
            "Compaction payload stored for round-trip.",
            "Compaction id: cmp-1",
        )
    )
    assert _format_compaction_progress(completed_part, provider_name="anthropic") == "\n".join(
        (
            "Provider: anthropic",
            "Status: history compacted",
            "Compaction completed.",
            "Compaction id: cmp-2",
        )
    )


def test_build_tool_updates_skips_final_result_and_projects_retry_prompts() -> None:
    updates = build_tool_updates(
        [
            ModelResponse(
                parts=[
                    BuiltinToolCallPart(
                        tool_name="final_result",
                        args={"answer": "done"},
                        tool_call_id="out-1",
                    ),
                    BuiltinToolReturnPart(
                        tool_name="final_result",
                        tool_call_id="out-1",
                        content="done",
                    ),
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        "retry search",
                        tool_name="web_search",
                        tool_call_id="retry-1",
                    )
                ]
            ),
        ],
        classifier=DefaultToolClassifier(),
        projection_map=BuiltinToolProjectionMap(),
        serializer=DefaultOutputSerializer(),
    )

    assert len(updates) == 1
    retry_update = updates[0]
    assert isinstance(retry_update, ToolCallProgress)
    assert retry_update.tool_call_id == "retry-1"
    assert retry_update.status == "failed"
    assert retry_update.kind == "search"
    assert isinstance(retry_update.raw_output, str)
    assert "retry search" in retry_update.raw_output
