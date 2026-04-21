from __future__ import annotations as _annotations

import os

from codex_auth_helper import create_codex_chat_openai
from langchain.agents import create_agent
from langchain_acp import run_acp

MODEL_NAME = os.getenv("CODEX_MODEL", "gpt-5.4")


def describe_codex_surface() -> str:
    """Describe the LangChain-facing Codex helper seam."""

    return (
        "This graph uses codex-auth-helper to build a LangChain ChatOpenAI model "
        "backed by the local Codex login."
    )


graph = create_agent(
    model=create_codex_chat_openai(MODEL_NAME),
    tools=[describe_codex_surface],
    name="codex-chat-openai-graph",
)


def main() -> None:
    run_acp(graph=graph)


if __name__ == "__main__":
    main()
