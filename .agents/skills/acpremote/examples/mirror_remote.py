from __future__ import annotations as _annotations

import asyncio
import os

from acp import run_agent
from acpremote import TransportOptions, connect_acp


def _remote_url() -> str:
    return os.getenv("ACPREMOTE_URL", "ws://127.0.0.1:8080/acp/ws")


def _bearer_token() -> str | None:
    token = os.getenv("ACPREMOTE_BEARER_TOKEN", "").strip()
    return token or None


async def main() -> None:
    agent = connect_acp(
        _remote_url(),
        bearer_token=_bearer_token(),
        options=TransportOptions(
            emit_latency_meta=True,
            emit_latency_projection=True,
        ),
    )
    await run_agent(agent)


if __name__ == "__main__":
    asyncio.run(main())
