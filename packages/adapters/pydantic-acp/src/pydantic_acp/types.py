from __future__ import annotations as _annotations

from typing import TypeAlias

from acp.interfaces import Agent as _AcpAgent
from acp.schema import (
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    HttpMcpServer,
    ImageContentBlock,
    McpServerStdio,
    PlanEntry,
    ResourceContentBlock,
    SseMcpServer,
    TextContentBlock,
    TextResourceContents,
)

AcpAgent: TypeAlias = _AcpAgent
AgentPromptBlock: TypeAlias = (
    TextContentBlock
    | ImageContentBlock
    | AudioContentBlock
    | ResourceContentBlock
    | EmbeddedResourceContentBlock
)

__all__ = (
    "AcpAgent",
    "AgentPromptBlock",
    "AudioContentBlock",
    "BlobResourceContents",
    "EmbeddedResourceContentBlock",
    "HttpMcpServer",
    "ImageContentBlock",
    "McpServerStdio",
    "PlanEntry",
    "ResourceContentBlock",
    "SseMcpServer",
    "TextContentBlock",
    "TextResourceContents",
)
