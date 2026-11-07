from __future__ import annotations as _annotations

import asyncio

__all__ = ("loop_has_stopped",)


def loop_has_stopped(
    previous: asyncio.AbstractEventLoop | None, current: asyncio.AbstractEventLoop
) -> bool:
    """Whether an async HTTP client last used on ``previous`` must be replaced on ``current``.

    An HTTP connection pool keeps the connections it opened bound to that loop. Once the loop has
    stopped, for example after ``asyncio.run`` returned, those connections can neither serve nor
    close on another loop. A loop that still runs is left alone: it may be serving the client
    concurrently, as Pydantic AI also assumes for the clients its providers own.
    """
    return previous is not None and previous is not current and not previous.is_running()
