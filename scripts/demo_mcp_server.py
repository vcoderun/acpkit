from __future__ import annotations as _annotations

from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("vcode-demo")


@mcp.tool()
def echo_text(text: str) -> str:
    """Echo text back to the caller."""
    return text


@mcp.tool()
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def utc_now() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    mcp.run()
