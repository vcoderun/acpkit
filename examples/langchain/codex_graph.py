from __future__ import annotations as _annotations

import os
from collections.abc import Callable

from codex_auth_helper import create_codex_chat_openai
from langchain.agents import create_agent
from langchain_acp import AdapterConfig, run_acp

__all__ = (
    "MODEL_NAME",
    "build_graph",
    "config",
    "describe_codex_surface",
    "main",
)

MODEL_NAME = os.getenv("CODEX_MODEL", "gpt-5.4")


def describe_codex_surface() -> str:
    """Summarize the Codex-backed LangChain example surface."""

    return "\n".join(
        (
            "Codex graph features:",
            "- LangChain ChatOpenAI wired through codex-auth-helper",
            "- OpenAI Responses API transport through local Codex auth state",
            "- ready for `langchain-acp` exposure through `run_acp(graph=...)`",
        )
    )


def _tools() -> tuple[Callable[[], str], ...]:
    return (describe_codex_surface,)


def build_graph() -> object:
    return create_agent(
        model=create_codex_chat_openai(MODEL_NAME),
        tools=list(_tools()),
        name="codex-graph",
    )


config = AdapterConfig()


def main() -> None:
    run_acp(graph=build_graph(), config=config)


if __name__ == "__main__":
    main()
