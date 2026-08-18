from __future__ import annotations as _annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AgentBridgeBuilder,
    AgentFactory,
    CapabilityBridge,
    FileSessionStore,
    HarnessCodeModeBridge,
    HarnessFileSystemBridge,
    HarnessShellBridge,
    create_acp_agent,
    run_acp,
)
from pydantic_ai import Agent

if TYPE_CHECKING:
    from codex_auth_helper import CodexResponsesModel

__all__ = ("acp_agent", "agent_factory", "config", "main")

_AGENT_NAME: Final[str] = "harness-agent"
_DEFAULT_MODEL_NAME: Final[str] = "openrouter:google/gemini-3-flash-preview"
_DEFAULT_CODEX_MODEL: Final[str] = "gpt-5.4"
_DEMO_ROOT: Final[Path] = Path("agent_demos")
_WORKSPACE_ROOT: Final[Path] = Path.cwd() / _DEMO_ROOT / "harness-agent"
_SESSION_STORE_ROOT: Final[Path] = (
    Path(os.getenv("ACP_EXAMPLE_SESSION_DIR", str(_DEMO_ROOT / "acp-sessions")))
    .expanduser()
    .resolve()
    / "pydantic-harness"
)
_INSTRUCTIONS: Final[str] = (
    "You are an ACP Kit harness agent. Use the pydantic-ai-harness filesystem and shell "
    "tools for concrete workspace work. Keep all filesystem and shell work inside the "
    "seeded harness workspace. Prefer inspecting existing files before editing or running "
    "commands, and summarize tool results plainly."
)
_CODE_MODE_INSTRUCTIONS: Final[str] = (
    " Code-mode tools are enabled for this run; use them only when Python execution or "
    "multi-step code inspection is actually useful."
)
_DEFAULT_FILES: Final[dict[str, str]] = {
    "README.md": (
        "# Harness Workspace\n\n"
        "Use the harness file and shell tools for real workspace operations here. "
        "Run this example with `--codemode` to enable code-mode tools.\n"
    ),
    "notes/todo.md": "- inspect README.md\n- run a harmless shell command\n- explain findings\n",
}


def _ensure_workspace() -> None:
    _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    for relative_path, content in _DEFAULT_FILES.items():
        file_path = _WORKSPACE_ROOT / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")


def _build_harness_bridges(
    root: Path,
    *,
    include_code_mode: bool = False,
) -> tuple[CapabilityBridge, ...]:
    bridges: list[CapabilityBridge] = [
        HarnessFileSystemBridge(
            root_dir=root,
            capability_id="workspace-files",
            description="Inspect and edit files inside the isolated agent workspace.",
            allowed_patterns=("**/*",),
            protected_patterns=(".git/*", ".env", ".env.*", "*.pem", "*.key"),
            max_read_lines=400,
            max_search_results=50,
            max_find_results=50,
        ),
        HarnessShellBridge(
            cwd=root,
            capability_id="workspace-shell",
            description="Run bounded commands inside the isolated agent workspace.",
            denied_commands=("rm", "mv", "cp", "curl", "wget", "git"),
            default_timeout=5.0,
            max_output_chars=8000,
            persist_cwd=False,
            allow_interactive=False,
        ),
    ]
    if include_code_mode:
        bridges.append(
            HarnessCodeModeBridge(
                max_retries=2,
                capability_id="workspace-code-mode",
                description="Execute typed CodeMode programs for workspace tasks.",
            )
        )
    return tuple(bridges)


def _instructions(*, include_code_mode: bool) -> str:
    if include_code_mode:
        return _INSTRUCTIONS + _CODE_MODE_INSTRUCTIONS
    return _INSTRUCTIONS


def _harness_model(*, instructions: str = _INSTRUCTIONS) -> str | CodexResponsesModel:
    configured_model = os.getenv("ACP_HARNESS_MODEL", "").strip()
    if configured_model:
        return configured_model
    configured_codex_model = os.getenv("ACP_HARNESS_CODEX_MODEL", "").strip()
    if not configured_codex_model:
        return _DEFAULT_MODEL_NAME
    try:
        from codex_auth_helper import create_codex_responses_model
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Install `codex-auth-helper`, unset `ACP_HARNESS_CODEX_MODEL`, or set "
            "`ACP_HARNESS_MODEL` to a pydantic-ai model name before running the harness example.",
        ) from exc
    return create_codex_responses_model(
        configured_codex_model or _DEFAULT_CODEX_MODEL,
        instructions=instructions,
    )


def _build_config(*, include_code_mode: bool) -> AdapterConfig:
    return AdapterConfig(
        agent_name=_AGENT_NAME,
        agent_title="Harness Agent",
        session_store=FileSessionStore(_SESSION_STORE_ROOT),
        capability_bridges=_build_harness_bridges(
            _WORKSPACE_ROOT,
            include_code_mode=include_code_mode,
        ),
    )


def _build_agent(session: AcpSessionContext, *, include_code_mode: bool) -> Agent[None, str]:
    """Build a real model-backed agent wired with pydantic-ai-harness capabilities."""
    _ensure_workspace()
    harness_bridges = _build_harness_bridges(
        _WORKSPACE_ROOT,
        include_code_mode=include_code_mode,
    )
    bridge_contributions = AgentBridgeBuilder(
        session=session,
        capability_bridges=harness_bridges,
    ).build()
    return Agent(
        _harness_model(instructions=_instructions(include_code_mode=include_code_mode)),
        name=_AGENT_NAME,
        capabilities=bridge_contributions.capabilities,
        instructions=_instructions(include_code_mode=include_code_mode),
    )


def _build_agent_factory(
    *,
    include_code_mode: bool,
) -> AgentFactory[None, str]:
    def factory(session: AcpSessionContext) -> Agent[None, str]:
        return _build_agent(session, include_code_mode=include_code_mode)

    return factory


def agent_factory(session: AcpSessionContext) -> Agent[None, str]:
    return _build_agent(session, include_code_mode=False)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ACP Kit harness example.")
    parser.add_argument(
        "--codemode",
        action="store_true",
        help="Enable the pydantic-ai-harness CodeMode capability bridge for this run.",
    )
    return parser.parse_args(argv)


_HARNESS_BRIDGES: Final[tuple[CapabilityBridge, ...]] = _build_harness_bridges(_WORKSPACE_ROOT)

config = _build_config(include_code_mode=False)
acp_agent = create_acp_agent(agent_factory=agent_factory, config=config)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(()) if argv is None else _parse_args(argv)
    runtime_config = config
    runtime_agent_factory: AgentFactory[None, str] = agent_factory
    if args.codemode:
        runtime_config = _build_config(include_code_mode=True)
        runtime_agent_factory = _build_agent_factory(include_code_mode=True)
    _ensure_workspace()
    run_acp(agent_factory=runtime_agent_factory, config=runtime_config)


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
