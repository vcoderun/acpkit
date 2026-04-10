from __future__ import annotations as _annotations

from collections.abc import Iterable

__all__ = (
    "MCP_SERVERS_COMMAND_NAME",
    "HOOKS_COMMAND_NAME",
    "MODEL_COMMAND_NAME",
    "RESERVED_SLASH_COMMAND_NAMES",
    "THINKING_COMMAND_NAME",
    "TOOLS_COMMAND_NAME",
    "validate_mode_command_ids",
)

MODEL_COMMAND_NAME = "model"
THINKING_COMMAND_NAME = "thinking"
TOOLS_COMMAND_NAME = "tools"
HOOKS_COMMAND_NAME = "hooks"
MCP_SERVERS_COMMAND_NAME = "mcp-servers"
RESERVED_SLASH_COMMAND_NAMES = frozenset(
    {
        MODEL_COMMAND_NAME,
        THINKING_COMMAND_NAME,
        TOOLS_COMMAND_NAME,
        HOOKS_COMMAND_NAME,
        MCP_SERVERS_COMMAND_NAME,
    }
)


def validate_mode_command_ids(mode_ids: Iterable[str]) -> None:
    normalized_ids: list[str] = []
    for mode_id in mode_ids:
        normalized_mode_id = mode_id.strip().lower()
        if not normalized_mode_id:
            raise ValueError("Mode ids must be non-empty and slash-command compatible.")
        if any(character.isspace() for character in normalized_mode_id):
            raise ValueError(f"Mode id {mode_id!r} is invalid. Mode ids cannot contain whitespace.")
        normalized_ids.append(normalized_mode_id)
    duplicate_ids = sorted(
        mode_id for mode_id in set(normalized_ids) if normalized_ids.count(mode_id) > 1
    )
    if duplicate_ids:
        duplicate_text = ", ".join(duplicate_ids)
        raise ValueError(
            f"Mode ids must be unique after normalization. Duplicate ids: {duplicate_text}."
        )
    conflicting_ids = sorted(set(normalized_ids) & RESERVED_SLASH_COMMAND_NAMES)
    if conflicting_ids:
        conflict_text = ", ".join(conflicting_ids)
        raise ValueError(
            "Mode ids cannot reuse reserved slash command names "
            f"({conflict_text}). Choose a more specific id outside the reserved keywords."
        )
