from __future__ import annotations as _annotations

import asyncio
import os

from langchain_acp import create_acp_agent

from acpkit import serve_acp
from examples.langchain.workspace_graph import config, graph_from_session

__all__ = ("main",)


def _host() -> str:
    return os.getenv("ACPREMOTE_HOST", "127.0.0.1")


def _port() -> int:
    return int(os.getenv("ACPREMOTE_PORT", "8080"))


def _mount_path() -> str:
    return os.getenv("ACPREMOTE_MOUNT_PATH", "/acp")


def _bearer_token() -> str | None:
    token = os.getenv("ACPREMOTE_BEARER_TOKEN", "").strip()
    return token or None


async def main() -> None:
    server = await serve_acp(
        create_acp_agent(graph_factory=graph_from_session, config=config),
        host=_host(),
        port=_port(),
        mount_path=_mount_path(),
        bearer_token=_bearer_token(),
    )
    try:
        await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
