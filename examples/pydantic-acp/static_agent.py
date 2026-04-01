from __future__ import annotations as _annotations

from pydantic_acp import run_acp
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

__all__ = ("agent", "main")


agent = Agent(
    TestModel(custom_output_text="Hello from the static pydantic-acp example."),
    name="static-example",
    system_prompt="Answer directly and keep responses short.",
)


@agent.tool_plain
def describe_adapter_surface() -> str:
    """Summarize the ACP adapter surface that this example exposes."""

    return "\n".join(
        (
            "This example demonstrates:",
            "- a direct Agent instance",
            "- adapter name inference from agent.name",
            "- static run_acp(agent=...) wiring",
        )
    )


def main() -> None:
    run_acp(agent=agent)


if __name__ == "__main__":
    main()
