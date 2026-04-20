from __future__ import annotations as _annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydantic_acp import (
    AdapterConfig,
    FileSystemProjectionMap,
    HookProjectionMap,
    MemorySessionStore,
    run_acp,
)
from pydantic_acp.models import ModelOverride
from pydantic_acp.types import (
    AgentPromptBlock,
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
)
from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests

__all__ = ("TravelPromptModelProvider", "agent", "config", "main")

_TRAVEL_ROOT: Final[Path] = Path(__file__).resolve().parent / ".travel-agent"
_READ_TOOL: Final[str] = "read_trip_file"
_WRITE_TOOL: Final[str] = "write_trip_file"
_MEDIA_MODEL_ENV_NAMES: Final[tuple[str, ...]] = (
    "ACP_TRAVEL_MEDIA_MODEL",
    "TRAVEL_MEDIA_MODEL",
)
_DEFAULT_FILES: Final[dict[str, str]] = {
    "itinerary.md": (
        "# Travel Brief\n\n"
        "- city: Lisbon\n"
        "- trip goal: design-heavy long weekend\n"
        "- constraints: walkable neighborhoods, low-friction transit\n"
    ),
    "ideas.txt": (
        "Try these prompts:\n"
        "- list trip files\n"
        "- read trip file itinerary.md\n"
        "- write trip file scratch.txt: book the riverside hotel\n"
    ),
}


def _default_model_name() -> str | TestModel:
    configured_model = os.getenv("MODEL_NAME", "").strip()
    if configured_model:
        return configured_model
    return TestModel()


def _configured_media_model_name() -> str | None:
    for env_name in _MEDIA_MODEL_ENV_NAMES:
        configured_model = os.getenv(env_name, "").strip()
        if configured_model:
            return configured_model
    return None


def _ensure_travel_workspace() -> None:
    _TRAVEL_ROOT.mkdir(parents=True, exist_ok=True)
    for relative_path, content in _DEFAULT_FILES.items():
        file_path = _resolve_trip_path(relative_path)
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")


def _resolve_trip_path(path: str) -> Path:
    candidate = (_TRAVEL_ROOT / path).resolve()
    try:
        candidate.relative_to(_TRAVEL_ROOT)
    except ValueError as exc:
        raise ValueError("Path must stay inside the travel demo workspace.") from exc
    return candidate


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...[truncated]"


def _prompt_has_binary_media(prompt: Sequence[AgentPromptBlock]) -> bool:
    for block in prompt:
        if isinstance(block, ImageContentBlock | AudioContentBlock):
            return True
        if isinstance(block, ResourceContentBlock):
            mime_type = block.mime_type
            if mime_type is not None and not mime_type.startswith("text/"):
                return True
            continue
        if isinstance(block, EmbeddedResourceContentBlock) and isinstance(
            block.resource, BlobResourceContents
        ):
            return True
    return False


def _prompt_has_image_media(prompt: Sequence[AgentPromptBlock]) -> bool:
    for block in prompt:
        if isinstance(block, ImageContentBlock):
            return True
        if isinstance(block, ResourceContentBlock):
            mime_type = block.mime_type
            if mime_type is not None and mime_type.startswith("image/"):
                return True
            continue
        if isinstance(block, EmbeddedResourceContentBlock):
            resource = block.resource
            if isinstance(resource, BlobResourceContents):
                mime_type = resource.mime_type
                if mime_type is not None and mime_type.startswith("image/"):
                    return True
    return False


class TravelPromptModelProvider:
    def get_prompt_model_override(
        self,
        session: Any,
        agent: Any,
        prompt: Sequence[AgentPromptBlock],
        model_override: ModelOverride | None,
    ) -> ModelOverride | None:
        del session, agent
        if not _prompt_has_binary_media(prompt):
            return model_override
        return _configured_media_model_name() or model_override


hooks = Hooks[None]()


@hooks.on.before_model_request
async def observe_before_model_request(ctx: Any, request_context: Any) -> Any:
    del ctx
    return request_context


@hooks.on.after_model_request
async def observe_after_model_request(
    ctx: Any,
    *,
    request_context: Any,
    response: Any,
) -> Any:
    del ctx, request_context
    return response


@hooks.on.before_tool_execute(tools=[_READ_TOOL])
async def observe_read_tool(ctx: Any, *, call: Any, tool_def: Any, args: Any) -> Any:
    del ctx, call, tool_def
    return args


@hooks.on.before_tool_execute(tools=[_WRITE_TOOL])
async def observe_write_tool(ctx: Any, *, call: Any, tool_def: Any, args: Any) -> Any:
    del ctx, call, tool_def
    return args


@hooks.on.after_tool_execute(tools=[_WRITE_TOOL])
async def observe_write_result(
    ctx: Any,
    *,
    call: Any,
    tool_def: Any,
    args: Any,
    result: Any,
) -> Any:
    del ctx, call, tool_def, args
    return result


agent = Agent(
    _default_model_name(),
    name="travel-agent",
    capabilities=[hooks],
    output_type=[str, DeferredToolRequests],
    system_prompt=(
        "You are the ACP Kit travel example. "
        "Use the trip-file tools for grounded answers. "
        "When image or audio prompt blocks appear, the host may swap the active model override."
    ),
)


@agent.tool_plain
def describe_travel_surface() -> str:
    """Summarize the ACP-facing surfaces available in this travel example."""

    return "\n".join(
        (
            "Travel example features:",
            "- existing Hooks capability introspection rendered through HookProjectionMap",
            "- approval-gated file writes with ACP diffs",
            "- prompt-model override provider for image and audio prompts",
        )
    )


@agent.tool_plain
def list_trip_files() -> str:
    """List the demo travel files available in the local workspace."""

    _ensure_travel_workspace()
    return "\n".join(sorted(path.name for path in _TRAVEL_ROOT.iterdir() if path.is_file()))


@agent.tool_plain(name=_READ_TOOL)
def read_trip_file(path: str, max_chars: int = 4000) -> str:
    """Read a travel file and return a bounded preview."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive.")
    _ensure_travel_workspace()
    file_path = _resolve_trip_path(Path(path).name if Path(path).is_absolute() else path)
    if not file_path.exists():
        raise ValueError(f"File not found: {file_path.name}")
    return _truncate_text(file_path.read_text(encoding="utf-8"), limit=max_chars)


@agent.tool_plain(name=_WRITE_TOOL, requires_approval=True)
def write_trip_file(path: str, content: str) -> str:
    """Write a travel file inside the local demo workspace."""

    _ensure_travel_workspace()
    file_path = _resolve_trip_path(Path(path).name if Path(path).is_absolute() else path)
    file_path.write_text(content, encoding="utf-8")
    return f"Wrote `{file_path.name}`."


config = AdapterConfig(
    session_store=MemorySessionStore(),
    prompt_model_override_provider=TravelPromptModelProvider(),
    hook_projection_map=HookProjectionMap(
        hidden_event_ids=frozenset({"after_model_request"}),
        event_labels={
            "before_model_request": "Before Model",
            "before_tool_execute": "Before Execute",
            "after_tool_execute": "After Execute",
        },
    ),
    projection_maps=[
        FileSystemProjectionMap(
            default_read_tool=_READ_TOOL,
            default_write_tool=_WRITE_TOOL,
        )
    ],
)


def main() -> None:
    _ensure_travel_workspace()
    run_acp(agent=agent, config=config)
