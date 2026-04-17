from __future__ import annotations as _annotations

from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, TypeVar
from urllib.parse import urlparse

from pydantic_ai import ModelRequestContext
from pydantic_ai.builtin_tools import ImageAspectRatio, WebSearchUserLocation
from pydantic_ai.capabilities import (
    MCP,
    AbstractCapability,
    ImageGeneration,
    IncludeToolReturnSchemas,
    PrefixTools,
    SetToolMetadata,
    ThreadExecutor,
    Toolset,
    WebFetch,
    WebSearch,
)
from pydantic_ai.messages import CompactionPart, ModelMessage, ModelResponse
from pydantic_ai.tools import RunContext, ToolSelector
from pydantic_ai.toolsets import AgentToolset

from ..agent_types import RuntimeAgent
from ..session.state import AcpSessionContext, JsonValue
from .base import BufferedCapabilityBridge, CapabilityBridge

if TYPE_CHECKING:
    from pydantic_ai.builtin_tools import ImageGenerationTool, MCPServerTool
    from pydantic_ai.models import KnownModelName, Model

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)

__all__ = (
    "AnthropicCompactionBridge",
    "ImageGenerationBridge",
    "IncludeToolReturnSchemasBridge",
    "McpCapabilityBridge",
    "OpenAICompactionBridge",
    "PrefixToolsBridge",
    "SetToolMetadataBridge",
    "ThreadExecutorBridge",
    "ToolsetBridge",
    "WebFetchBridge",
    "WebSearchBridge",
)

_DEFAULT_WEB_SEARCH_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "duckduckgo_search",
        "exa_search",
        "tavily_search",
        "web_search",
    }
)
_DEFAULT_WEB_FETCH_TOOL_NAMES: Final[frozenset[str]] = frozenset({"web_fetch"})
_DEFAULT_IMAGE_GENERATION_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"generate_image", "image_generation"}
)
_DEFAULT_MCP_TOOL_NAME_PREFIXES: Final[frozenset[str]] = frozenset({"mcp_server:"})


def _json_string_list(values: frozenset[str] | list[str] | None) -> list[JsonValue] | None:
    if values is None:
        return None
    result: list[JsonValue] = []
    result.extend(sorted(values))
    return result


def _json_user_location(location: WebSearchUserLocation | None) -> dict[str, JsonValue] | None:
    if location is None:
        return None
    metadata: dict[str, JsonValue] = {}
    for key, value in location.items():
        if value is None or isinstance(value, bool | int | float | str):
            metadata[str(key)] = value
    return metadata or None


def _json_bool(value: bool) -> JsonValue:
    return value


def _json_string(value: str | None) -> JsonValue | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _json_int(value: int | None) -> JsonValue | None:
    return value


def _resolve_mcp_server_id(url: str, explicit_id: str | None) -> str:
    if explicit_id:
        return explicit_id
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    host = parsed.hostname or ""
    if slug:
        return f"{host}-{slug}" if host else slug
    return host or url


@dataclass(slots=True, kw_only=True)
class ThreadExecutorBridge(CapabilityBridge):
    executor: Executor
    metadata_key: str | None = "thread_executor"

    def build_capability(self, session: AcpSessionContext) -> ThreadExecutor:
        del session
        return ThreadExecutor(self.executor)

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {"executor_type": type(self.executor).__name__}


@dataclass(slots=True)
class SetToolMetadataBridge(CapabilityBridge, Generic[AgentDepsT]):
    tools: ToolSelector[AgentDepsT] = "all"
    metadata_key: str | None = None
    metadata: dict[str, JsonValue] | None = None

    def __init__(
        self,
        *,
        tools: ToolSelector[AgentDepsT] = "all",
        metadata_key: str | None = None,
        **metadata: JsonValue,
    ) -> None:
        self.tools = tools
        self.metadata_key = metadata_key
        self.metadata = dict(metadata)

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> SetToolMetadata[AgentDepsT]:
        del session
        metadata = self.metadata or {}
        return SetToolMetadata(
            tools=self.tools,
            **metadata,
        )

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)


@dataclass(slots=True)
class IncludeToolReturnSchemasBridge(CapabilityBridge, Generic[AgentDepsT]):
    tools: ToolSelector[AgentDepsT] = "all"
    metadata_key: str | None = None

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> IncludeToolReturnSchemas[AgentDepsT]:
        del session
        return IncludeToolReturnSchemas(tools=self.tools)

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)


@dataclass(slots=True, kw_only=True)
class ImageGenerationBridge(CapabilityBridge, Generic[AgentDepsT]):
    builtin: bool | ImageGenerationTool | Any = True
    local: Any = None
    fallback_model: Model | KnownModelName | str | Callable[..., Any] | None = None
    background: Literal["transparent", "opaque", "auto"] | None = None
    input_fidelity: Literal["high", "low"] | None = None
    moderation: Literal["auto", "low"] | None = None
    output_compression: int | None = None
    output_format: Literal["png", "webp", "jpeg"] | None = None
    quality: Literal["low", "medium", "high", "auto"] | None = None
    size: Literal["auto", "1024x1024", "1024x1536", "1536x1024", "512", "1K", "2K", "4K"] | None = (
        None
    )
    aspect_ratio: ImageAspectRatio | None = None
    tool_names: frozenset[str] = _DEFAULT_IMAGE_GENERATION_TOOL_NAMES
    metadata_key: str | None = "image_generation"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> ImageGeneration[AgentDepsT]:
        del session
        return ImageGeneration(
            builtin=self.builtin,
            local=self.local,
            fallback_model=self.fallback_model,
            background=self.background,
            input_fidelity=self.input_fidelity,
            moderation=self.moderation,
            output_compression=self.output_compression,
            output_format=self.output_format,
            quality=self.quality,
            size=self.size,
            aspect_ratio=self.aspect_ratio,
        )

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {
            "aspect_ratio": _json_string(self.aspect_ratio),
            "background": _json_string(self.background),
            "fallback_model": _json_string(
                self.fallback_model if isinstance(self.fallback_model, str) else None
            ),
            "input_fidelity": _json_string(self.input_fidelity),
            "moderation": _json_string(self.moderation),
            "output_compression": _json_int(self.output_compression),
            "output_format": _json_string(self.output_format),
            "quality": _json_string(self.quality),
            "size": _json_string(self.size),
            "tool_names": _json_string_list(self.tool_names),
        }

    def get_tool_kind(
        self, tool_name: str, raw_input: JsonValue | None = None
    ) -> Literal["execute"] | None:
        del raw_input
        return "execute" if tool_name in self.tool_names else None


@dataclass(slots=True, kw_only=True)
class McpCapabilityBridge(CapabilityBridge, Generic[AgentDepsT]):
    url: str
    builtin: bool | MCPServerTool | Any = True
    local: Any = None
    id: str | None = None
    authorization_token: str | None = None
    headers: dict[str, str] | None = None
    allowed_tools: list[str] | None = None
    description: str | None = None
    tool_name_prefixes: frozenset[str] = _DEFAULT_MCP_TOOL_NAME_PREFIXES
    metadata_key: str | None = "mcp_capability"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> MCP[AgentDepsT]:
        del session
        return MCP(
            self.url,
            builtin=self.builtin,
            local=self.local,
            id=self.id,
            authorization_token=self.authorization_token,
            headers=self.headers,
            allowed_tools=self.allowed_tools,
            description=self.description,
        )

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {
            "allowed_tools": _json_string_list(self.allowed_tools),
            "description": _json_string(self.description),
            "has_authorization_token": _json_bool(self.authorization_token is not None),
            "headers": _json_string_list(sorted((self.headers or {}).keys())),
            "server_id": _resolve_mcp_server_id(self.url, self.id),
            "tool_name_prefixes": _json_string_list(self.tool_name_prefixes),
            "url": self.url,
        }

    def get_tool_kind(
        self, tool_name: str, raw_input: JsonValue | None = None
    ) -> Literal["execute"] | None:
        del raw_input
        return (
            "execute"
            if any(tool_name.startswith(prefix) for prefix in self.tool_name_prefixes)
            else None
        )


@dataclass(slots=True, kw_only=True)
class ToolsetBridge(CapabilityBridge, Generic[AgentDepsT]):
    toolset: AgentToolset[AgentDepsT]
    metadata_key: str | None = "toolset"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> Toolset[AgentDepsT]:
        del session
        return Toolset(toolset=self.toolset)

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        toolset_id = getattr(self.toolset, "id", None)
        return {
            "toolset_id": _json_string(toolset_id if isinstance(toolset_id, str) else None),
            "toolset_type": type(self.toolset).__name__,
        }


@dataclass(slots=True, kw_only=True)
class PrefixToolsBridge(CapabilityBridge, Generic[AgentDepsT]):
    wrapped: AbstractCapability[AgentDepsT]
    prefix: str
    metadata_key: str | None = "prefix_tools"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> PrefixTools[AgentDepsT]:
        del session
        return PrefixTools(wrapped=self.wrapped, prefix=self.prefix)

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {
            "prefix": self.prefix,
            "wrapped_capability": type(self.wrapped).__name__,
        }

    def get_tool_kind(
        self, tool_name: str, raw_input: JsonValue | None = None
    ) -> Literal["execute"] | None:
        del raw_input
        return "execute" if tool_name.startswith(f"{self.prefix}_") else None


@dataclass(slots=True, kw_only=True)
class OpenAICompactionBridge(BufferedCapabilityBridge, Generic[AgentDepsT]):
    message_count_threshold: int | None = None
    trigger: Callable[[list[ModelMessage]], bool] | None = None
    instructions: str | None = None
    metadata_key: str | None = "openai_compaction"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> AbstractCapability[Any]:
        from pydantic_ai.models.openai import OpenAICompaction

        bridge = self

        class _BridgeOpenAICompaction(OpenAICompaction[Any]):
            def __init__(self) -> None:
                super().__init__(
                    message_count_threshold=bridge.message_count_threshold,
                    trigger=bridge.trigger,
                    instructions=bridge.instructions,
                )

            async def before_model_request(
                self,
                ctx: RunContext[Any],
                request_context: ModelRequestContext,
            ) -> ModelRequestContext:
                if not _should_openai_compact(
                    request_context.messages,
                    trigger=self.trigger,
                    message_count_threshold=self.message_count_threshold,
                ):
                    return request_context

                event_id = bridge._record_started_event(
                    session,
                    title="Context Compaction",
                    raw_input={
                        "provider": "openai",
                        "instructions": _json_string(self.instructions),
                        "message_count": len(request_context.messages),
                    },
                )
                try:
                    updated_context = await super().before_model_request(ctx, request_context)
                except Exception as error:
                    bridge._record_progress_event(
                        session,
                        event_id=event_id,
                        title="Context Compaction",
                        status="failed",
                        raw_output=f"Provider: openai\nStatus: failed\nError: {error}",
                    )
                    raise

                bridge._record_progress_event(
                    session,
                    event_id=event_id,
                    title="Context Compaction",
                    status="completed",
                    raw_output=_format_openai_compaction_output(updated_context),
                )
                return updated_context

            @classmethod
            def get_serialization_name(cls) -> str | None:
                return "OpenAICompaction"

        return _BridgeOpenAICompaction()

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {
            "has_trigger": _json_bool(self.trigger is not None),
            "instructions": _json_string(self.instructions),
            "message_count_threshold": _json_int(self.message_count_threshold),
        }


@dataclass(slots=True, kw_only=True)
class AnthropicCompactionBridge(CapabilityBridge, Generic[AgentDepsT]):
    token_threshold: int = 150_000
    instructions: str | None = None
    pause_after_compaction: bool = False
    metadata_key: str | None = "anthropic_compaction"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> AbstractCapability[Any]:
        del session
        from pydantic_ai.models.anthropic import AnthropicCompaction

        return AnthropicCompaction(
            token_threshold=self.token_threshold,
            instructions=self.instructions,
            pause_after_compaction=self.pause_after_compaction,
        )

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {
            "instructions": _json_string(self.instructions),
            "pause_after_compaction": _json_bool(self.pause_after_compaction),
            "token_threshold": self.token_threshold,
        }


def _should_openai_compact(
    messages: list[ModelMessage],
    *,
    trigger: Callable[[list[ModelMessage]], bool] | None,
    message_count_threshold: int | None,
) -> bool:
    if trigger is not None:
        return trigger(messages)
    if message_count_threshold is not None:
        return len(messages) > message_count_threshold
    return False


def _format_openai_compaction_output(request_context: ModelRequestContext) -> str:
    parts: list[str] = [
        "Provider: openai",
        "Status: history compacted",
        "Compaction payload stored for round-trip.",
    ]
    compacted_part = _extract_compaction_part(request_context.messages)
    if compacted_part is not None and compacted_part.id:
        parts.append(f"Compaction id: {compacted_part.id}")
    return "\n".join(parts)


def _extract_compaction_part(messages: list[ModelMessage]) -> CompactionPart | None:
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, CompactionPart):
                return part
    return None


@dataclass(slots=True, kw_only=True)
class WebSearchBridge(CapabilityBridge, Generic[AgentDepsT]):
    builtin: bool | Any = True
    local: Any = None
    search_context_size: Literal["low", "medium", "high"] | None = None
    user_location: WebSearchUserLocation | None = None
    blocked_domains: list[str] | None = None
    allowed_domains: list[str] | None = None
    max_uses: int | None = None
    tool_names: frozenset[str] = _DEFAULT_WEB_SEARCH_TOOL_NAMES
    metadata_key: str | None = "web_search"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> WebSearch[AgentDepsT]:
        del session
        return WebSearch(
            builtin=self.builtin,
            local=self.local,
            search_context_size=self.search_context_size,
            user_location=self.user_location,
            blocked_domains=self.blocked_domains,
            allowed_domains=self.allowed_domains,
            max_uses=self.max_uses,
        )

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {
            "allowed_domains": _json_string_list(self.allowed_domains),
            "blocked_domains": _json_string_list(self.blocked_domains),
            "max_uses": self.max_uses,
            "search_context_size": self.search_context_size,
            "tool_names": _json_string_list(self.tool_names),
            "user_location": _json_user_location(self.user_location),
        }

    def get_tool_kind(
        self, tool_name: str, raw_input: JsonValue | None = None
    ) -> Literal["search"] | None:
        del raw_input
        return "search" if tool_name in self.tool_names else None


@dataclass(slots=True, kw_only=True)
class WebFetchBridge(CapabilityBridge, Generic[AgentDepsT]):
    builtin: bool | Any = True
    local: Any = None
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    max_uses: int | None = None
    enable_citations: bool | None = None
    max_content_tokens: int | None = None
    tool_names: frozenset[str] = _DEFAULT_WEB_FETCH_TOOL_NAMES
    metadata_key: str | None = "web_fetch"

    def build_capability(
        self,
        session: AcpSessionContext,
    ) -> WebFetch[AgentDepsT]:
        del session
        return WebFetch(
            builtin=self.builtin,
            local=self.local,
            allowed_domains=self.allowed_domains,
            blocked_domains=self.blocked_domains,
            max_uses=self.max_uses,
            enable_citations=self.enable_citations,
            max_content_tokens=self.max_content_tokens,
        )

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (self.build_capability(session),)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        return {
            "allowed_domains": _json_string_list(self.allowed_domains),
            "blocked_domains": _json_string_list(self.blocked_domains),
            "enable_citations": self.enable_citations,
            "max_content_tokens": self.max_content_tokens,
            "max_uses": self.max_uses,
            "tool_names": _json_string_list(self.tool_names),
        }

    def get_tool_kind(
        self, tool_name: str, raw_input: JsonValue | None = None
    ) -> Literal["fetch"] | None:
        del raw_input
        return "fetch" if tool_name in self.tool_names else None
