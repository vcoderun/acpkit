from __future__ import annotations as _annotations

import os
from pathlib import Path
from typing import Final

from pydantic_acp import run_acp
from pydantic_ai import Agent, ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests

__all__ = ("agent",)

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional developer convenience.
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent
_SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "references",
    }
)
_DEMO_NOTES: dict[str, str] = {
    "adapter-status": (
        "Implemented milestones: bare ACP adapter, session-local model selection, "
        "native deferred approval bridge."
    ),
    "demo-ideas": (
        "Ask me to read a repo file, search repo paths, inspect supported capabilities, "
        "update a demo note, or delete a demo note."
    ),
    "session-reminder": (
        "The ACP adapter persists transcript and model message history per session."
    ),
}


def _list_notes_text() -> str:
    rendered_notes = [
        f"- {name}: {_truncate_text(content, limit=80)}"
        for name, content in sorted(_DEMO_NOTES.items())
    ]
    if not rendered_notes:
        return "No demo notes are stored yet."
    return "\n".join(("Available demo notes:", *rendered_notes))


def _normalize_note_name(name: str) -> str:
    normalized = "-".join(name.strip().lower().split())
    if not normalized:
        raise ValueError("Note name cannot be empty.")
    return normalized


def _resolve_repo_path(path: str) -> Path:
    candidate = (_REPO_ROOT / path).resolve()
    try:
        candidate.relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise ValueError("Path must stay inside the repository root.") from exc
    if not candidate.is_file():
        raise ValueError(f"File not found: {path}")
    return candidate


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...[truncated]"


def _iter_repo_paths() -> list[Path]:
    paths: list[Path] = []
    for root, dir_names, file_names in os.walk(_REPO_ROOT):
        dir_names[:] = [name for name in dir_names if name not in _SKIP_DIR_NAMES]
        root_path = Path(root)
        for file_name in file_names:
            candidate = root_path / file_name
            paths.append(candidate)
    return paths


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

    rendered_returns = [f"{part.tool_name}: {part.content}" for part in tool_returns]
    return ModelResponse(parts=[TextPart("\n".join(rendered_returns))])


def _call_tool(tool_name: str, **kwargs: str | int) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name, kwargs)])


def _demo_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    del info

    tool_result_response = _tool_result_response(messages)
    if tool_result_response is not None:
        return tool_result_response

    prompt = _latest_user_prompt(messages).strip()
    lowered_prompt = prompt.lower()

    if "capabilities" in lowered_prompt or "what can you do" in lowered_prompt:
        return _call_tool("fetch_supported_capabilities")
    if lowered_prompt in {
        "notları oku",
        "notlari oku",
        "notlari listele",
        "notları listele",
    }:
        return _call_tool("list_notes")
    if lowered_prompt.startswith("search repo "):
        return _call_tool("search_repo_paths", query=prompt[12:].strip())
    if lowered_prompt.startswith("read repo file "):
        return _call_tool("read_repo_file", path=prompt[15:].strip())
    if lowered_prompt.startswith("read file "):
        return _call_tool("read_repo_file", path=prompt[10:].strip())
    if lowered_prompt.startswith("read note "):
        return _call_tool("read_note", name=prompt[10:].strip())
    if lowered_prompt.startswith("list notes"):
        return _call_tool("list_notes")
    if lowered_prompt.startswith("search notes "):
        return _call_tool("search_notes", query=prompt[13:].strip())
    if lowered_prompt.startswith("update note "):
        payload = prompt[12:].strip()
        if ":" in payload:
            name, content = payload.split(":", 1)
            return _call_tool("update_note", name=name.strip(), content=content.strip())
        if " with " in payload:
            name, content = payload.split(" with ", 1)
            return _call_tool("update_note", name=name.strip(), content=content.strip())
        return ModelResponse(
            parts=[
                TextPart(
                    "Use `update note <name>: <content>` or `update note <name> with <content>`."
                )
            ]
        )
    if lowered_prompt.startswith("delete note "):
        return _call_tool("delete_note", name=prompt[12:].strip())

    return ModelResponse(
        parts=[
            TextPart(
                "\n".join(
                    (
                        "Demo mode is active.",
                        "Try one of these prompts:",
                        "- capabilities",
                        "- search repo spec",
                        "- read repo file README.md",
                        "- read note adapter-status",
                        "- search notes session",
                        "- update note scratch: hello world",
                        "- delete note scratch",
                    )
                )
            )
        ]
    )


def _build_model() -> str | FunctionModel:
    configured_model = os.getenv("ACP_DEMO_MODEL")
    if configured_model:
        return configured_model
    return FunctionModel(_demo_model, model_name="acpkit-demo-function-model")


agent = Agent(
    _build_model(),
    name="acpkit_demo_agent",
    output_type=[str, DeferredToolRequests],
    system_prompt=(
        "You are the ACP Kit demo agent. Use tools whenever the user asks to inspect repo files, "
        "search the workspace, or work with demo notes. Mutating tools may require approval; "
        "when that happens, let the host approval flow handle it instead of inventing success."
    ),
)


@agent.tool_plain
def fetch_supported_capabilities() -> str:
    """Return the adapter capabilities this demo agent is meant to exercise."""

    return "\n".join(
        (
            "Supported adapter demo paths:",
            "- normal prompt/response turns",
            "- transcript replay on load/resume",
            "- generic tool projection for read/search/fetch/edit/delete style tools",
            "- native deferred approval flow for approval-gated tools",
            "- module auto-detection via `acpkit run my_agent` or `acpkit run my_agent:agent`",
        )
    )


@agent.tool_plain
def search_repo_paths(query: str) -> str:
    """Search repository-relative file paths by substring and return the first matches."""

    normalized_query = query.strip().lower()
    if not normalized_query:
        top_level_paths = sorted(
            path.relative_to(_REPO_ROOT).as_posix() for path in _REPO_ROOT.iterdir()
        )
        return "\n".join(("Query was empty. Top-level repo paths:", *top_level_paths[:20]))

    matches: list[str] = []
    for path in _iter_repo_paths():
        relative_path = path.relative_to(_REPO_ROOT).as_posix()
        if normalized_query in relative_path.lower():
            matches.append(relative_path)
        if len(matches) >= 20:
            break

    if not matches:
        return f"No repo paths matched `{query}`."
    return "\n".join(matches)


@agent.tool_plain
def read_repo_file(path: str, max_chars: int = 4000) -> str:
    """Read a repository file relative to this repo root and return a bounded text preview."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive.")

    file_path = _resolve_repo_path(path)
    text = file_path.read_text(encoding="utf-8")
    return _truncate_text(text, limit=max_chars)


@agent.tool_plain
def read_note(name: str) -> str:
    """Read a demo note from the in-process note store."""

    if not name.strip():
        return _list_notes_text()
    note_name = _normalize_note_name(name)
    note = _DEMO_NOTES.get(note_name)
    if note is None:
        return f"No note named `{note_name}`.\n\n{_list_notes_text()}"
    return note


@agent.tool_plain
def search_notes(query: str) -> str:
    """Search demo notes by name or content and return matching note names."""

    normalized_query = query.strip().lower()
    if not normalized_query:
        return _list_notes_text()

    matches = [
        name
        for name, content in sorted(_DEMO_NOTES.items())
        if normalized_query in name or normalized_query in content.lower()
    ]
    if not matches:
        return f"No demo notes matched `{query}`.\n\n{_list_notes_text()}"
    return "\n".join(matches)


@agent.tool_plain
def list_notes() -> str:
    """List the available demo notes with short previews."""

    return _list_notes_text()


@agent.tool_plain(requires_approval=True)
def update_note(name: str, content: str) -> str:
    """Create or replace a demo note. This tool always goes through ACP approval."""

    note_name = _normalize_note_name(name)
    _DEMO_NOTES[note_name] = content.strip()
    return f"Updated note `{note_name}`."


@agent.tool_plain(requires_approval=True)
def delete_note(name: str) -> str:
    """Delete a demo note. This tool always goes through ACP approval."""

    note_name = _normalize_note_name(name)
    removed = _DEMO_NOTES.pop(note_name, None)
    if removed is None:
        return f"Note `{note_name}` was already absent."
    return f"Deleted note `{note_name}`."


if __name__ == "__main__":
    run_acp(agent=agent)
