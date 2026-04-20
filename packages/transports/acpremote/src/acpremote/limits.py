from __future__ import annotations as _annotations

from typing import Final

__all__ = (
    "DEFAULT_CLOSE_TIMEOUT",
    "DEFAULT_MAX_MESSAGE_SIZE",
    "DEFAULT_MAX_QUEUE",
    "DEFAULT_OPEN_TIMEOUT",
    "DEFAULT_PING_INTERVAL",
    "DEFAULT_PING_TIMEOUT",
)

DEFAULT_MAX_MESSAGE_SIZE: Final[int] = 1_048_576
DEFAULT_MAX_QUEUE: Final[int] = 16
DEFAULT_OPEN_TIMEOUT: Final[float] = 10.0
DEFAULT_PING_INTERVAL: Final[float] = 20.0
DEFAULT_PING_TIMEOUT: Final[float] = 20.0
DEFAULT_CLOSE_TIMEOUT: Final[float] = 10.0
