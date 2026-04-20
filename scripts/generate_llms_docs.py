from __future__ import annotations as _annotations

import re
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "https://vcoderun.github.io/acpkit/"


@dataclass(frozen=True, kw_only=True)
class DocPage:
    section: str
    title: str
    summary: str
    path: str

    @property
    def url(self) -> str:
        relative_path = self.path.removeprefix("docs/")
        if relative_path == "index.md":
            return BASE_URL
        if relative_path.endswith("/index.md"):
            return BASE_URL + relative_path.removesuffix("index.md")
        return BASE_URL + relative_path.removesuffix(".md") + "/"


DOC_PAGES: tuple[DocPage, ...] = (
    DocPage(
        section="Overview",
        title="ACP Kit",
        summary="Product overview, package map, truthful ACP exposure, and recommended reading order.",
        path="docs/index.md",
    ),
    DocPage(
        section="Getting Started",
        title="Installation",
        summary="Production and development installation paths for the root package and optional extras.",
        path="docs/getting-started/installation.md",
    ),
    DocPage(
        section="Getting Started",
        title="Quickstart Hub",
        summary="Adapter chooser for the Pydantic and LangChain quickstart paths, plus shared next steps.",
        path="docs/getting-started/quickstart.md",
    ),
    DocPage(
        section="Getting Started",
        title="Pydantic Quickstart",
        summary="Smallest runnable path from a normal Pydantic AI agent to an ACP server.",
        path="docs/getting-started/pydantic-quickstart.md",
    ),
    DocPage(
        section="Getting Started",
        title="LangChain Quickstart",
        summary="Smallest runnable path from a LangChain or LangGraph graph to an ACP server.",
        path="docs/getting-started/langchain-quickstart.md",
    ),
    DocPage(
        section="Getting Started",
        title="CLI",
        summary="Target resolution, acpkit run and launch semantics, and failure modes.",
        path="docs/cli.md",
    ),
    DocPage(
        section="Core Docs",
        title="Pydantic ACP Overview",
        summary="Adapter purpose, construction seams, and when to reach for each runtime integration path.",
        path="docs/pydantic-acp.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain ACP Overview",
        summary="Graph-centric adapter overview, production config examples, DeepAgents compatibility, migration notes, and maintained LangChain examples.",
        path="docs/langchain-acp.md",
    ),
    DocPage(
        section="Core Docs",
        title="AdapterConfig",
        summary="Field-by-field guide to runtime configuration, ownership, and adapter behavior.",
        path="docs/pydantic-acp/adapter-config.md",
    ),
    DocPage(
        section="Core Docs",
        title="Session State and Lifecycle",
        summary="Session stores, replay semantics, persistence, and state transitions.",
        path="docs/pydantic-acp/session-state.md",
    ),
    DocPage(
        section="Core Docs",
        title="Models, Modes, and Slash Commands",
        summary="Model selection, dynamic mode switching, thinking effort, and slash command semantics.",
        path="docs/pydantic-acp/runtime-controls.md",
    ),
    DocPage(
        section="Core Docs",
        title="Plans, Thinking, and Approvals",
        summary="Native plan state, approval flows, cancellation, and thinking capability behavior.",
        path="docs/pydantic-acp/plans-thinking-approvals.md",
    ),
    DocPage(
        section="Core Docs",
        title="Prompt Resources and Context",
        summary="Resource links, embedded context, Zed selections, branch diffs, and multimodal prompt input behavior.",
        path="docs/pydantic-acp/prompt-resources.md",
    ),
    DocPage(
        section="Core Docs",
        title="Providers",
        summary="Host-owned models, modes, config, plan persistence, and approval metadata patterns.",
        path="docs/providers.md",
    ),
    DocPage(
        section="Core Docs",
        title="Bridges",
        summary="Capability bridges for prepare-tools, thinking, hooks, MCP metadata, and history processors.",
        path="docs/bridges.md",
    ),
    DocPage(
        section="Core Docs",
        title="Host Backends and Projections",
        summary="Client-backed filesystem, terminal execution, and projection map rendering.",
        path="docs/host-backends.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain AdapterConfig",
        summary="Field-by-field guide to graph-centric adapter configuration, projection maps, and runtime ownership.",
        path="docs/langchain-acp/adapter-config.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain Session State and Lifecycle",
        summary="Session stores, transcript replay, graph rebuild semantics, and persisted state handling.",
        path="docs/langchain-acp/session-state.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain Models, Modes, and Config",
        summary="Model selection, mode selection, and config-option ownership for LangChain and LangGraph runtimes.",
        path="docs/langchain-acp/runtime-controls.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain Plans, Thinking, and Approvals",
        summary="Native ACP plans, `write_todos` compatibility, thinking settings, and HITL approval bridging.",
        path="docs/langchain-acp/plans-thinking-approvals.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain Prompt Resources and Context",
        summary="Prompt conversion, multimodal input mapping, resources, and context materialization for graph prompts.",
        path="docs/langchain-acp/prompt-resources.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain Providers",
        summary="Host-owned models, modes, config options, and native plan persistence seams for `langchain-acp`.",
        path="docs/langchain-acp/providers.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain Bridges",
        summary="Capability bridges, graph build contributions, DeepAgents compatibility, and tool-surface shaping seams.",
        path="docs/langchain-acp/bridges.md",
    ),
    DocPage(
        section="Core Docs",
        title="LangChain Projections and Event Projection Maps",
        summary="Projection presets for filesystem, search, browser, HTTP, finance, and event rendering in `langchain-acp`.",
        path="docs/langchain-acp/projections.md",
    ),
    DocPage(
        section="Core Docs",
        title="Helpers",
        summary="Supporting packages such as `codex-auth-helper` and `acpremote` that sit around the adapter runtimes without replacing them.",
        path="docs/helpers.md",
    ),
    DocPage(
        section="Core Docs",
        title="ACP Remote Overview",
        summary="Generic ACP WebSocket transport, stdio command mirroring, acpkit serve/run-addr flow, metadata routes, and proxy-observed timing.",
        path="docs/acpremote.md",
    ),
    DocPage(
        section="Examples",
        title="Examples Overview",
        summary="Maintained examples across the Pydantic and LangChain adapters, plus the recommended reading order.",
        path="docs/examples/index.md",
    ),
    DocPage(
        section="Examples",
        title="Finance Agent",
        summary="Session-aware finance workspace with ACP plans, approvals, mode-aware tool shaping, and projected note diffs.",
        path="docs/examples/finance.md",
    ),
    DocPage(
        section="Examples",
        title="Travel Agent",
        summary="Travel planning runtime with hook projection, approval-gated trip files, and prompt-model override behavior for media prompts.",
        path="docs/examples/travel.md",
    ),
    DocPage(
        section="Examples",
        title="LangChain Workspace Graph",
        summary="Maintained plain-LangChain example with module-level graph wiring and filesystem read projection.",
        path="docs/examples/langchain-workspace.md",
    ),
    DocPage(
        section="Examples",
        title="DeepAgents Compatibility Example",
        summary="Maintained DeepAgents-facing example using the compatibility bridge and DeepAgents projection preset.",
        path="docs/examples/deepagents.md",
    ),
    DocPage(
        section="Examples",
        title="Dynamic Factory Agents",
        summary="Session-aware `agent_factory(session)` patterns for dynamic agent creation, parameter routing, and when to step up to `AgentSource`.",
        path="docs/examples/dynamic-factory.md",
    ),
    DocPage(
        section="API Reference",
        title="acpkit API",
        summary="API reference for the root CLI package and target resolution helpers.",
        path="docs/api/acpkit.md",
    ),
    DocPage(
        section="API Reference",
        title="ACP Remote API",
        summary="API reference for the remote transport package, command mirroring, and connection helpers.",
        path="docs/api/acpremote.md",
    ),
    DocPage(
        section="API Reference",
        title="langchain_acp API",
        summary="API reference for the graph adapter package, providers, bridges, plans, and projection helpers.",
        path="docs/api/langchain_acp.md",
    ),
    DocPage(
        section="API Reference",
        title="pydantic_acp API",
        summary="API reference for the adapter package, session stores, providers, bridges, and helpers.",
        path="docs/api/pydantic_acp.md",
    ),
    DocPage(
        section="API Reference",
        title="codex_auth_helper API",
        summary="API reference for Codex auth state, client construction, and Responses model helpers.",
        path="docs/api/codex_auth_helper.md",
    ),
    DocPage(
        section="Operations",
        title="Testing",
        summary="Canonical validation commands, coverage entry points, and test style guidance.",
        path="docs/testing.md",
    ),
    DocPage(
        section="Operations",
        title="About ACP Kit",
        summary="Design goals, intended audience, package scope, and project status.",
        path="docs/about/index.md",
    ),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _strip_frontmatter(markdown_text: str) -> str:
    if not markdown_text.startswith("---\n"):
        return markdown_text
    _, _, remainder = markdown_text.partition("\n---\n")
    return remainder


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _html_to_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", text).strip()


def _expand_partials(markdown_text: str) -> str:
    repo_root = _repo_root()

    def replace(match: re.Match[str]) -> str:
        relative_path = match.group(1)
        partial_path = repo_root / relative_path
        partial_text = partial_path.read_text(encoding="utf-8")
        if partial_path.suffix == ".html":
            return _html_to_text(partial_text)
        return partial_text

    return re.sub(r'--8<--\s+"([^"]+)"', replace, markdown_text)


def _load_doc_source(page: DocPage) -> str:
    doc_path = _repo_root() / page.path
    markdown_text = doc_path.read_text(encoding="utf-8")
    markdown_text = _strip_frontmatter(markdown_text)
    markdown_text = _expand_partials(markdown_text)
    return _collapse_blank_lines(markdown_text)


def _grouped_pages() -> dict[str, list[DocPage]]:
    grouped: dict[str, list[DocPage]] = {}
    for page in DOC_PAGES:
        grouped.setdefault(page.section, []).append(page)
    return grouped


def _build_llms_index() -> str:
    lines: list[str] = [
        "# ACP Kit",
        "",
        f"Published docs base URL: {BASE_URL}",
        "",
        "ACP Kit is a Python SDK and CLI that turns an existing agent surface into a truthful ACP server boundary.",
        "Today the repo ships production-grade adapters for both Pydantic AI and the LangChain stack, plus helper packages for Codex auth and ACP transport.",
        "",
        "## Documentation Index",
        "",
    ]
    for section, pages in _grouped_pages().items():
        lines.append(f"### {section}")
        lines.append("")
        for page in pages:
            lines.extend(
                (
                    f"- [{page.title}]({page.url})",
                    f"  Source: `{page.path}`",
                    f"  Summary: {page.summary}",
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_llms_full() -> str:
    lines: list[str] = [
        "# ACP Kit Full Docs Context",
        "",
        f"Published docs base URL: {BASE_URL}",
        "",
        "ACP Kit is a Python SDK and CLI that turns an existing agent surface into a truthful ACP server boundary.",
        "This file inlines the current documentation corpus so tools and agents can reason over the same docs users read on the published site.",
        "",
        "## Documentation Index",
        "",
    ]
    for section, pages in _grouped_pages().items():
        lines.append(f"### {section}")
        lines.append("")
        for page in pages:
            lines.append(f"- [{page.title}]({page.url})")
        lines.append("")
    lines.extend(("## Full Documents", ""))
    for page in DOC_PAGES:
        lines.extend(
            (
                f"### {page.title}",
                f"URL: {page.url}",
                f"Source: `{page.path}`",
                "",
                _load_doc_source(page),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    repo_root = _repo_root()
    (repo_root / "docs" / "llms.txt").write_text(_build_llms_index(), encoding="utf-8")
    (repo_root / "docs" / "llms-full.txt").write_text(_build_llms_full(), encoding="utf-8")


if __name__ == "__main__":
    main()
