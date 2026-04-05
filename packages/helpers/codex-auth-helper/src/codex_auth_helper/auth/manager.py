from __future__ import annotations as _annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .config import CodexAuthConfig
from .state import CodexAuthState
from .store import CodexAuthStore

__all__ = ("CodexTokenManager",)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _response_mapping(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Expected the token endpoint to return an object.")
    return payload


def _string_value(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


@dataclass(slots=True)
class CodexTokenManager:
    config: CodexAuthConfig
    store: CodexAuthStore
    http_client: httpx.AsyncClient
    owns_http_client: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _state: CodexAuthState = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._state = self.store.read_state()

    @property
    def current_state(self) -> CodexAuthState:
        return self._state

    @property
    def current_account_id(self) -> str | None:
        return self._state.account_id

    async def close(self) -> None:
        if self.owns_http_client:
            await self.http_client.aclose()

    async def get_access_token(self) -> str:
        async with self._lock:
            if self._should_refresh(self._state):
                self._state = await self._refresh_locked()
            return self._state.access_token

    async def prepare_account_header(self, request: httpx.Request) -> None:
        if request.url.host != "chatgpt.com":
            return
        account_id = self.current_account_id
        if account_id is not None:
            request.headers["ChatGPT-Account-Id"] = account_id

    def _refresh_deadline(self, state: CodexAuthState) -> datetime | None:
        if state.expires_at is not None:
            return state.expires_at
        if state.last_refresh is not None:
            return state.last_refresh + self.config.default_token_ttl
        return None

    def _should_refresh(self, state: CodexAuthState) -> bool:
        deadline = self._refresh_deadline(state)
        if deadline is None:
            return False
        return deadline <= _now_utc() + self.config.refresh_margin

    async def _refresh_locked(self) -> CodexAuthState:
        response = await self.http_client.post(
            f"{self.config.issuer}/oauth/token",
            content=str(
                httpx.QueryParams(
                    {
                        "client_id": self.config.client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": self._state.refresh_token,
                    }
                )
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()

        payload = _response_mapping(response)
        refreshed_state = CodexAuthState.from_json_dict(
            {
                "OPENAI_API_KEY": self._state.openai_api_key,
                "auth_mode": self._state.auth_mode,
                "last_refresh": _now_utc().isoformat().replace("+00:00", "Z"),
                "tokens": {
                    "access_token": _string_value(payload, "access_token"),
                    "account_id": _string_value(payload, "account_id") or self._state.account_id,
                    "id_token": _string_value(payload, "id_token") or self._state.id_token,
                    "refresh_token": _string_value(payload, "refresh_token")
                    or self._state.refresh_token,
                },
            }
        )
        self.store.write_state(refreshed_state)
        return refreshed_state
