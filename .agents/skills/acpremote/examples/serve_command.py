from __future__ import annotations as _annotations

import asyncio
import os
import shlex

from acpremote import serve_command


def _command() -> tuple[str, ...]:
    raw = os.getenv("ACPREMOTE_COMMAND", "").strip()
    if raw:
        return tuple(shlex.split(raw))
    return (
        "acpkit",
        "run",
        "examples.langchain.workspace_graph:graph",
    )


def _host() -> str:
    return os.getenv("ACPREMOTE_HOST", "127.0.0.1")


def _port() -> int:
    return int(os.getenv("ACPREMOTE_PORT", "8080"))


async def main() -> None:
    server = await serve_command(
        _command(),
        host=_host(),
        port=_port(),
    )
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
