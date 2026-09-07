from __future__ import annotations as _annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Final, cast

TURN_STATE_HEADER: Final = "x-codex-turn-state"


@dataclass
class ResponsesTurnState:
    value: str | None = field(default=None, repr=False)
    closed: bool = False

    def capture(self, headers: object) -> None:
        if self.closed or self.value is not None or not isinstance(headers, Mapping):
            return
        typed_headers = cast(Mapping[str, object], headers)
        for key in typed_headers:
            if str(key).lower() != TURN_STATE_HEADER:
                continue
            try:
                value = typed_headers[key]
            except LookupError:
                return
            if (
                isinstance(value, str)
                and value.strip()
                and len(value) <= 8192
                and value.isascii()
                and all(ord(char) >= 32 and ord(char) != 127 for char in value)
            ):
                self.value = value
            return

    def apply(self, values: Mapping[str, Any] | None) -> dict[str, Any]:
        result = {
            key: value for key, value in (values or {}).items() if key.lower() != TURN_STATE_HEADER
        }
        if not self.closed and self.value is not None:
            result[TURN_STATE_HEADER] = self.value
        return result


class ResponsesTurnContext:
    def __init__(self) -> None:
        self._state: ContextVar[ResponsesTurnState | None] = ContextVar(
            f"codex_responses_turn_{id(self)}", default=None
        )

    def current(self) -> ResponsesTurnState | None:
        state = self._state.get()
        return state if state is not None and not state.closed else None

    @contextmanager
    def scope(self) -> Iterator[None]:
        state = ResponsesTurnState()
        token = self._state.set(state)
        try:
            yield
        finally:
            state.closed = True
            state.value = None
            self._state.reset(token)
