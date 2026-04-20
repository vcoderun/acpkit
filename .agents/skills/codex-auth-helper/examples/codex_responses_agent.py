from __future__ import annotations as _annotations

import os

from codex_auth_helper import create_codex_responses_model
from pydantic_acp import run_acp
from pydantic_ai import Agent

MODEL_NAME = os.getenv("CODEX_MODEL", "gpt-5.4")

agent = Agent(
    create_codex_responses_model(MODEL_NAME),
    name="codex-responses-agent",
    instructions=(
        "You are a concise coding assistant. Ask for clarification when the task is underspecified."
    ),
)


def main() -> None:
    run_acp(agent=agent)


if __name__ == "__main__":
    main()
