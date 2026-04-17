from __future__ import annotations as _annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from acp import run_agent
from acp.interfaces import Agent as AcpAgent
from acp.schema import (
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    FileSessionStore,
    FileSystemProjectionMap,
    RuntimeAgent,
    ThinkingBridge,
    create_acp_agent,
    truncate_text,
)
from pydantic_acp.models import ModelOverride
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

__all__ = ("WorkspacePromptModelProvider", "build_agent", "build_server_agent", "main")

WorkspacePromptBlock = (
    AudioContentBlock
    | EmbeddedResourceContentBlock
    | ImageContentBlock
    | ResourceContentBlock
    | TextContentBlock
)

_DEFAULT_MODEL: Final[str] = "openai:gpt-5.4-mini"
_MEDIA_MODEL_ENV_NAMES: Final[tuple[str, ...]] = ("ACP_MEDIA_MODEL", "MEDIA_MODEL_NAME")
_READ_REPO_TOOL: Final[str] = "read_repo_file"
_SESSION_STORE_DIR: Final[Path] = Path(".acp-sessions")
_SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".acpkit",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)


def _default_model() -> str | TestModel:
    configured_model = os.getenv("MODEL_NAME", "").strip()
    if configured_model:
        return configured_model
    return TestModel(
        call_tools=["describe_media_surface"],
        custom_output_text="Media routing example ready.",
    )


def _configured_media_model_name() -> str | None:
    for env_name in _MEDIA_MODEL_ENV_NAMES:
        configured_model = os.getenv(env_name, "").strip()
        if configured_model != "":
            return configured_model
    return None


def _prompt_has_binary_media(prompt: Sequence[WorkspacePromptBlock]) -> bool:
    for block in prompt:
        if isinstance(block, ImageContentBlock | AudioContentBlock):
            return True
        if isinstance(block, ResourceContentBlock):
            mime_type = block.mime_type
            if mime_type is not None and not mime_type.startswith("text/"):
                return True
            continue
        if isinstance(block, EmbeddedResourceContentBlock):
            resource = block.resource
            if isinstance(resource, BlobResourceContents):
                return True
            if isinstance(resource, TextResourceContents):
                continue
    return False


def _prompt_has_image_media(prompt: Sequence[WorkspacePromptBlock]) -> bool:
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


def _has_google_media_fallback_credentials() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip())


def _google_media_fallback_model_name(model_name: str) -> str | None:
    normalized = model_name.removeprefix("openrouter:")
    if not normalized.startswith("google/"):
        return None
    return f"google-gla:{normalized.removeprefix('google/')}"


def _resolve_repo_file(repo_root: Path, path: str) -> Path:
    candidate = (repo_root / path).resolve()
    try:
        relative_path = candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("Path must stay inside the workspace root.") from exc
    if relative_path.parts and relative_path.parts[0] in _SKIP_DIR_NAMES:
        raise ValueError(f"`{relative_path.parts[0]}` is not exposed through the example tool.")
    if not candidate.is_file():
        raise ValueError(f"File not found: {path}")
    return candidate


@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspacePromptModelProvider:
    def get_prompt_model_override(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        prompt: Sequence[WorkspacePromptBlock],
        model_override: ModelOverride | None,
    ) -> ModelOverride | None:
        del session, agent
        if not _prompt_has_binary_media(prompt):
            return model_override
        if _prompt_has_image_media(prompt):
            active_model_name = str(model_override or os.getenv("MODEL_NAME", _DEFAULT_MODEL))
            fallback_model_name = _google_media_fallback_model_name(active_model_name)
            if fallback_model_name is not None and _has_google_media_fallback_credentials():
                return fallback_model_name
        media_model_name = _configured_media_model_name()
        if media_model_name is None:
            return model_override
        return media_model_name


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    repo_root = session.cwd.resolve()
    agent = Agent(
        _default_model(),
        name="workspace-media-example",
        system_prompt=(
            "You are the ACP Kit media-routing example. "
            "If the prompt includes image or audio blocks, the host may swap the underlying model override. "
            "Use repository tools only for grounded inspection."
        ),
    )

    @agent.tool_plain
    def describe_media_surface() -> str:
        """Summarize the media-routing behavior exposed by this example."""

        media_override = _configured_media_model_name() or "none"
        return "\n".join(
            (
                "Media routing example features:",
                "- prompt-model override provider for image and audio prompts",
                f"- configured media override: {media_override}",
                f"- default model: {os.getenv('MODEL_NAME', _DEFAULT_MODEL)}",
                "- ACP file projection for repository reads",
            )
        )

    @agent.tool_plain(name=_READ_REPO_TOOL)
    def read_repo_file(path: str, max_chars: int = 4000) -> str:
        """Read a repository file relative to the current workspace root."""

        if max_chars <= 0:
            raise ValueError("max_chars must be positive.")
        file_path = _resolve_repo_file(repo_root, path)
        return truncate_text(
            file_path.read_text(encoding="utf-8"),
            limit=max_chars,
        )

    return agent


def build_server_agent() -> AcpAgent:
    session_store_dir = Path.cwd() / _SESSION_STORE_DIR
    session_store_dir.mkdir(parents=True, exist_ok=True)
    return create_acp_agent(
        agent_factory=build_agent,
        config=AdapterConfig(
            capability_bridges=[ThinkingBridge()],
            prompt_model_override_provider=WorkspacePromptModelProvider(),
            projection_maps=[FileSystemProjectionMap(default_read_tool=_READ_REPO_TOOL)],
            session_store=FileSessionStore(session_store_dir),
        ),
    )


def main() -> None:
    asyncio.run(run_agent(build_server_agent()))


if __name__ == "__main__":
    main()
