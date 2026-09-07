from __future__ import annotations as _annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from hashlib import sha256
from typing import Any, Literal, TypeAlias, cast

from openai._models import FinalRequestOptions

__all__ = (
    "CodexResponsesTransport",
    "RESPONSES_LITE_HEADER",
)

CodexResponsesTransport: TypeAlias = Literal["responses", "responses_lite"]

RESPONSES_LITE_HEADER = "x-openai-internal-codex-responses-lite"


def prepare_responses_lite_options(options: FinalRequestOptions) -> FinalRequestOptions:
    if options.url.rstrip("/") != "/responses":
        return options
    if not isinstance(options.json_data, Mapping):
        return options

    body = cast("Mapping[str, Any]", options.json_data)
    if options.extra_json is not None:
        body = _merge_mappings(
            body,
            cast("Mapping[str, Any]", options.extra_json),
        )
        options.extra_json = None
    options.json_data = prepare_responses_lite_body(body)
    return options


def prepare_responses_lite_body(body: Mapping[str, Any]) -> dict[str, Any]:
    prepared = deepcopy(dict(body))
    if _is_prepared_lite_input(prepared.get("input")):
        return prepared

    instructions = prepared.pop("instructions", None)
    tools = _as_sequence(prepared.pop("tools", None), name="tools")
    input_items = _input_items(prepared.get("input"))

    prefix: list[dict[str, Any]] = [
        {
            "id": _stable_id("at", tools),
            "type": "additional_tools",
            "role": "developer",
            "tools": _lite_tools(tools),
        },
    ]
    if isinstance(instructions, str) and instructions:
        prefix.append(
            {
                "id": _stable_id("msg", instructions),
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": instructions}],
                "internal_chat_message_metadata_passthrough": {
                    "content_item_kinds": ["model.base_instructions"],
                },
            },
        )

    prepared["input"] = [*prefix, *input_items]
    prepared["parallel_tool_calls"] = False
    reasoning = prepared.get("reasoning")
    prepared_reasoning = dict(reasoning) if isinstance(reasoning, Mapping) else {}
    prepared_reasoning["context"] = "all_turns"
    prepared["reasoning"] = prepared_reasoning
    return prepared


def _as_sequence(value: Any, *, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"Responses Lite `{name}` must be a sequence.")
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError(f"Responses Lite `{name}` items must be objects.")
        items.append(deepcopy(dict(item)))
    return items


def _input_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return deepcopy(list(value))
    raise TypeError("Responses Lite `input` must be text or a sequence of input items.")


def _lite_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    lite_tools: list[dict[str, Any]] = []
    function_tools: list[dict[str, Any]] = []
    functions_index: int | None = None

    for tool in tools:
        tool_copy = deepcopy(dict(tool))
        tool_type = tool_copy.get("type")
        if tool_type in {"function", "custom"}:
            if tool_copy.get("description") is None:
                tool_copy["description"] = ""
            function_tools.append(tool_copy)
        elif tool_type == "namespace" and tool_copy.get("name") == "functions":
            nested = _as_sequence(tool_copy.get("tools"), name="namespace tools")
            function_tools.extend(nested)
        else:
            lite_tools.append(tool_copy)
            continue
        if functions_index is None:
            functions_index = len(lite_tools)

    if functions_index is not None and function_tools:
        lite_tools.insert(
            functions_index,
            {
                "type": "namespace",
                "name": "functions",
                "description": "",
                "tools": function_tools,
            },
        )
    return lite_tools


def _stable_id(prefix: str, value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}_{sha256(payload).hexdigest()[:32]}"


def _is_prepared_lite_input(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and bool(value)
        and isinstance(value[0], Mapping)
        and value[0].get("type") == "additional_tools"
    )


def _merge_mappings(
    base: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_mappings(existing, value)
        else:
            merged[key] = deepcopy(value)
    return merged
