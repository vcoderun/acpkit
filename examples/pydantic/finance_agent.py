from __future__ import annotations as _annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydantic_acp import (
    AdapterConfig,
    FileSystemProjectionMap,
    MemorySessionStore,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    ThinkingBridge,
    run_acp,
    truncate_text,
)
from pydantic_acp.providers import NativePlanPersistenceProvider
from pydantic_acp.types import PlanEntry
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, RunContext, ToolDefinition

__all__ = ("FinancePlanPersistenceProvider", "agent", "config", "main")

_FINANCE_DIR_NAME: Final[str] = ".finance-agent"
_NOTES_DIR_NAME: Final[str] = "notes"
_PLAN_DIR: Final[Path] = Path(".acpkit") / "plans"
_WATCHLIST_TOOL: Final[str] = "list_watchlist"
_READ_NOTE_TOOL: Final[str] = "read_market_note"
_WRITE_NOTE_TOOL: Final[str] = "save_market_note"
_QUOTE_TOOL: Final[str] = "quote_symbol"
_READ_PREVIEW_CHARS: Final[int] = 4000
_MUTATING_TOOLS: Final[frozenset[str]] = frozenset({_WRITE_NOTE_TOOL})
_DEFAULT_FILES: Final[dict[str, str]] = {
    "watchlist.md": "# Finance Watchlist\n\n- NVDA\n- MSFT\n- AAPL\n",
    f"{_NOTES_DIR_NAME}/daily-brief.md": (
        "# Daily Brief\n\nFocus on position sizing, liquidity, and explicit risk limits.\n"
    ),
}
_QUOTE_BOOK: Final[dict[str, str]] = {
    "AAPL": "AAPL 192.10 USD | bias: neutral",
    "MSFT": "MSFT 428.55 USD | bias: bullish",
    "NVDA": "NVDA 118.42 USD | bias: high-volatility",
}


def _workspace_model_name() -> str | TestModel:
    configured_model = os.getenv("ACP_FINANCE_MODEL", "").strip()
    if configured_model:
        return configured_model
    return TestModel()


def _finance_root(cwd: Path) -> Path:
    return cwd.resolve() / _FINANCE_DIR_NAME


def _ensure_finance_workspace(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in _DEFAULT_FILES.items():
        file_path = _resolve_market_path(root, relative_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")


def _resolve_market_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path must stay inside the finance workspace.") from exc
    return candidate


def _list_market_files(root: Path) -> str:
    _ensure_finance_workspace(root)
    file_names = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )
    return "\n".join(file_names)


def _read_market_note(root: Path, path: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive.")
    _ensure_finance_workspace(root)
    file_path = _resolve_market_path(root, path)
    if not file_path.exists():
        raise ValueError(f"File not found: {path}")
    return truncate_text(file_path.read_text(encoding="utf-8"), limit=max_chars)


def _save_market_note(root: Path, path: str, content: str) -> str:
    _ensure_finance_workspace(root)
    file_path = _resolve_market_path(root, path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return f"Saved `{file_path.relative_to(root).as_posix()}`."


def _render_plan_snapshot(
    *,
    entries: Sequence[PlanEntry],
    plan_markdown: str | None,
) -> str:
    if not entries:
        return plan_markdown or "No finance plan has been recorded yet."
    numbered_entries = "\n".join(
        f"{index}. [{entry.status}] ({entry.priority}) {entry.content}"
        for index, entry in enumerate(entries, start=1)
    )
    if not plan_markdown:
        return "\n\n".join(("Current finance plan entries:", numbered_entries))
    return "\n\n".join((plan_markdown.rstrip(), "Current finance plan entries:", numbered_entries))


def _read_only_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if tool_def.name not in _MUTATING_TOOLS]


def _trade_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


class FinancePlanPersistenceProvider(NativePlanPersistenceProvider):
    def persist_plan_state(
        self,
        session: Any,
        agent: Any,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        del agent
        storage_path = session.cwd / _PLAN_DIR / f"{session.session_id}.md"
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(
            _render_plan_snapshot(entries=entries, plan_markdown=plan_markdown),
            encoding="utf-8",
        )


agent = Agent(
    _workspace_model_name(),
    name="finance-agent",
    output_type=[str, DeferredToolRequests],
    instructions=(
        "You are the ACP Kit finance example. "
        "Use the finance workspace tools for grounded answers. "
        "When plan mode is active, return a real ACP task plan. "
        "Only update notes when the mutating tool is visible and ACP approvals allow it."
    ),
)


@agent.tool_plain
def describe_finance_surface() -> str:
    """Summarize the ACP-facing features available in this finance example."""

    return "\n".join(
        (
            "Finance example features:",
            "- ask/plan/trade tool modes via PrepareToolsBridge",
            "- structured native plan generation in plan mode",
            "- approval-gated note writes with ACP file diffs",
            "- persisted plan snapshots under .acpkit/plans/",
        )
    )


@agent.tool_plain(name=_WATCHLIST_TOOL)
def list_watchlist() -> str:
    """List the seeded watchlist and workspace note files."""

    finance_root = _finance_root(Path.cwd())
    return "\n\n".join(
        (
            "Seeded symbols:\n" + "\n".join(f"- {symbol}" for symbol in sorted(_QUOTE_BOOK)),
            "Workspace files:\n" + _list_market_files(finance_root),
        )
    )


@agent.tool_plain(name=_QUOTE_TOOL)
def quote_symbol(symbol: str) -> str:
    """Return a deterministic demo quote for a watchlist symbol."""

    normalized_symbol = symbol.strip().upper()
    try:
        return _QUOTE_BOOK[normalized_symbol]
    except KeyError as exc:
        raise ValueError(f"Unknown demo symbol: {symbol}") from exc


@agent.tool_plain(name=_READ_NOTE_TOOL)
def read_market_note(path: str, max_chars: int = _READ_PREVIEW_CHARS) -> str:
    """Read a finance workspace note relative to the current working directory."""

    return _read_market_note(_finance_root(Path.cwd()), path, max_chars=max_chars)


@agent.tool_plain(name=_WRITE_NOTE_TOOL, requires_approval=True)
def save_market_note(path: str, content: str) -> str:
    """Write a finance note inside the local workspace."""

    return _save_market_note(_finance_root(Path.cwd()), path, content)


config = AdapterConfig(
    session_store=MemorySessionStore(),
    capability_bridges=[
        ThinkingBridge(),
        PrepareToolsBridge(
            default_mode_id="ask",
            default_plan_generation_type="structured",
            modes=[
                PrepareToolsMode(
                    id="ask",
                    name="Ask",
                    description="Inspect watchlists and notes without mutations.",
                    prepare_func=_read_only_tools,
                ),
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    description="Return a structured ACP plan for research or portfolio work.",
                    prepare_func=_read_only_tools,
                    plan_mode=True,
                ),
                PrepareToolsMode(
                    id="trade",
                    name="Trade",
                    description="Allow approval-gated note updates after research is complete.",
                    prepare_func=_trade_tools,
                    plan_tools=True,
                ),
            ],
        ),
    ],
    approval_bridge=NativeApprovalBridge(),
    native_plan_persistence_provider=FinancePlanPersistenceProvider(),
    projection_maps=[
        FileSystemProjectionMap(
            default_read_tool=_READ_NOTE_TOOL,
            default_write_tool=_WRITE_NOTE_TOOL,
        )
    ],
)


def main() -> None:
    _ensure_finance_workspace(_finance_root(Path.cwd()))
    run_acp(agent=agent, config=config)
