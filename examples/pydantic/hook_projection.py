from __future__ import annotations as _annotations

from pathlib import Path
from typing import Final

from pydantic_acp import AdapterConfig, FileSystemProjectionMap, HookProjectionMap, run_acp
from pydantic_ai import Agent, ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests

__all__ = ("agent", "main")

_DEMO_ROOT: Final[Path] = Path(__file__).resolve().parent / ".hook-projection-demo"
_DEFAULT_FILES: Final[dict[str, str]] = {
    "status.md": (
        "# Hook Projection Demo\n\n"
        "This file exists so ACP can show read and write diffs while the native Hooks "
        "capability emits hook lifecycle updates.\n"
    ),
    "ideas.txt": (
        "Try these prompts:\n"
        "- capabilities\n"
        "- list demo files\n"
        "- read demo file status.md\n"
        "- write demo file scratch.txt: hello from ACP\n"
    ),
}


def _ensure_demo_workspace() -> None:
    _DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    for relative_path, content in _DEFAULT_FILES.items():
        file_path = _resolve_demo_path(relative_path)
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")


def _resolve_demo_path(path: str) -> Path:
    candidate = (_DEMO_ROOT / path).resolve()
    try:
        candidate.relative_to(_DEMO_ROOT)
    except ValueError as exc:
        raise ValueError("Path must stay inside the hook projection demo workspace.") from exc
    return candidate


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...[truncated]"


def _latest_user_prompt(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return ""


def _tool_result_response(messages: list[ModelMessage]) -> ModelResponse | None:
    if not messages or not isinstance(messages[-1], ModelRequest):
        return None
    tool_returns = [part for part in messages[-1].parts if isinstance(part, ToolReturnPart)]
    if not tool_returns:
        return None
    return ModelResponse(
        parts=[TextPart("\n".join(f"{part.tool_name}: {part.content}" for part in tool_returns))]
    )


def _call_tool(tool_name: str, **kwargs: str | int) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name, kwargs)])


def _route_prompt(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    del info
    tool_result_response = _tool_result_response(messages)
    if tool_result_response is not None:
        return tool_result_response

    prompt = _latest_user_prompt(messages).strip()
    lowered_prompt = prompt.lower()

    if "capabilities" in lowered_prompt or "what can you do" in lowered_prompt:
        return _call_tool("describe_demo_surface")
    if lowered_prompt in {"list demo files", "demo files", "list files"}:
        return _call_tool("list_demo_files")
    if lowered_prompt.startswith("read demo file "):
        path = prompt[15:].strip()
        return _call_tool("read_demo_file", path=str(_resolve_demo_path(path)))
    if lowered_prompt.startswith("write demo file "):
        payload = prompt[16:].strip()
        if ":" not in payload:
            return ModelResponse(
                parts=[TextPart("Use `write demo file <name>: <content>` to trigger a diff.")]
            )
        relative_path, content = payload.split(":", 1)
        return _call_tool(
            "write_demo_file",
            path=str(_resolve_demo_path(relative_path.strip())),
            content=content.strip(),
        )

    return ModelResponse(
        parts=[
            TextPart(
                "\n".join(
                    (
                        "HookProjectionMap demo mode is active.",
                        "Try one of these prompts:",
                        "- capabilities",
                        "- list demo files",
                        "- read demo file status.md",
                        "- write demo file scratch.txt: hello from ACP",
                    )
                )
            )
        ]
    )


hooks = Hooks[None]()


@hooks.on.before_model_request
async def observe_before_model_request(ctx, request_context):
    del ctx
    return request_context


@hooks.on.after_model_request
async def observe_after_model_request(ctx, *, request_context, response):
    del ctx, request_context
    return response


@hooks.on.before_tool_execute(tools=["read_demo_file"])
async def observe_read_tool(ctx, *, call, tool_def, args):
    del ctx, call, tool_def
    return args


@hooks.on.before_tool_execute(tools=["write_demo_file"])
async def observe_write_tool(ctx, *, call, tool_def, args):
    del ctx, call, tool_def
    return args


@hooks.on.after_tool_execute(tools=["write_demo_file"])
async def observe_write_result(ctx, *, call, tool_def, args, result):
    del ctx, call, tool_def, args
    return result


agent = Agent(
    FunctionModel(_route_prompt, model_name="hook-projection-demo-router"),
    name="hook-projection-demo",
    capabilities=[hooks],
    output_type=[str, DeferredToolRequests],
    system_prompt=(
        "You are a native pydantic-ai ACP demo agent. "
        "Use the demo file tools to show hook updates, approval-gated writes, and file diffs."
    ),
)


@agent.tool_plain
def describe_demo_surface() -> str:
    """Summarize the surfaces this example exercises."""

    return "\n".join(
        (
            "This demo exercises:",
            "- existing Hooks capability introspection",
            "- HookProjectionMap title and visibility customization",
            "- file read diff projection",
            "- approval-gated file write diff projection",
        )
    )


@agent.tool_plain
def list_demo_files() -> str:
    """List the demo files available in the local workspace."""

    _ensure_demo_workspace()
    file_names = sorted(path.name for path in _DEMO_ROOT.iterdir() if path.is_file())
    return "\n".join(file_names)


@agent.tool_plain
def read_demo_file(path: str, max_chars: int = 4000) -> str:
    """Read a demo file and return a bounded preview."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive.")
    _ensure_demo_workspace()
    file_path = _resolve_demo_path(Path(path).name if Path(path).is_absolute() else path)
    if not file_path.exists():
        raise ValueError(f"File not found: {file_path.name}")
    return _truncate_text(file_path.read_text(encoding="utf-8"), limit=max_chars)


@agent.tool_plain(requires_approval=True)
def write_demo_file(path: str, content: str) -> str:
    """Write a demo file inside the local workspace."""

    _ensure_demo_workspace()
    file_path = _resolve_demo_path(Path(path).name if Path(path).is_absolute() else path)
    file_path.write_text(content, encoding="utf-8")
    return f"Wrote `{file_path.name}`."


def main() -> None:
    _ensure_demo_workspace()
    run_acp(
        agent=agent,
        config=AdapterConfig(
            hook_projection_map=HookProjectionMap(
                hidden_event_ids=frozenset({"after_model_request"}),
                event_labels={
                    "before_model_request": "Before Model",
                    "before_tool_execute": "Before Execute",
                    "after_tool_execute": "After Execute",
                },
            )
        ),
        projection_maps=(
            FileSystemProjectionMap(
                default_read_tool="read_demo_file",
                default_write_tool="write_demo_file",
            ),
        ),
    )


if __name__ == "__main__":
    main()
