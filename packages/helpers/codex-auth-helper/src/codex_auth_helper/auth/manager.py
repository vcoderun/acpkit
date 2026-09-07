from __future__ import annotations as _annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from weakref import WeakValueDictionary

import httpx

from .config import CodexAuthConfig
from .errors import CodexAuthAccountMismatchError, CodexAuthRefreshError
from .state import CodexAuthState, JsonValue, _extract_account_id
from .store import CodexAuthStore

__all__ = ("CodexTokenManager",)

_REFRESH_LOCKS: WeakValueDictionary[str, Lock] = WeakValueDictionary()
_REFRESH_LOCKS_GUARD = Lock()


def _shared_refresh_lock(store: CodexAuthStore) -> Lock:
    key = str(store.path.resolve())
    with _REFRESH_LOCKS_GUARD:
        lock = _REFRESH_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _REFRESH_LOCKS[key] = lock
        return lock


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _response_mapping(response: httpx.Response) -> dict[str, JsonValue]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Expected the token endpoint to return an object.")
    return payload


def _string_value(data: Mapping[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


@dataclass(slots=True)
class CodexTokenManager:
    config: CodexAuthConfig
    store: CodexAuthStore
    http_client: httpx.AsyncClient
    owns_http_client: bool = False
    _refresh_lock: Lock = field(init=False, repr=False)
    _state: CodexAuthState = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._state = self.store.read_state()
        self._refresh_lock = _shared_refresh_lock(self.store)

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
        while not self._refresh_lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            if self._should_refresh(self._state):
                self._reload_guarded()
                if self._should_refresh(self._state):
                    self._state = await self._refresh_locked()
            return self._state.access_token
        finally:
            self._refresh_lock.release()

    def get_access_token_sync(self) -> str:
        with self._refresh_lock:
            if self._should_refresh(self._state):
                self._reload_guarded()
                if self._should_refresh(self._state):
                    self._state = self._refresh_locked_sync()
            return self._state.access_token

    async def recover_after_unauthorized(self, rejected_token: str, *, refresh: bool) -> str | None:
        """Reload first; optionally refresh once after an explicit 401 rejection."""
        while not self._refresh_lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            self._reload_guarded()
            if self._state.access_token != rejected_token and not self._should_refresh(self._state):
                return self._state.access_token
            if refresh:
                self._state = await self._refresh_locked()
                return self._state.access_token
            return None
        finally:
            self._refresh_lock.release()

    def recover_after_unauthorized_sync(self, rejected_token: str, *, refresh: bool) -> str | None:
        """Synchronous equivalent of the bounded unauthorized recovery step."""
        with self._refresh_lock:
            self._reload_guarded()
            if self._state.access_token != rejected_token and not self._should_refresh(self._state):
                return self._state.access_token
            if refresh:
                self._state = self._refresh_locked_sync()
                return self._state.access_token
            return None

    def _reload_guarded(self) -> None:
        state = self.store.read_state()
        self._guard_account(state.account_id)
        self._state = state

    def _guard_account(self, account_id: str | None) -> None:
        if account_id != self._state.account_id:
            raise CodexAuthAccountMismatchError(
                "Codex credentials changed account; create a new client after signing in."
            )

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
                    },
                ),
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return self._accept_refresh(response)

    def _refresh_locked_sync(self) -> CodexAuthState:
        # Sync callers should not reuse the shared AsyncClient from the normal helper path.
        # A short-lived sync client keeps the sync LangChain path explicit and isolated.
        with httpx.Client(
            follow_redirects=True,
            timeout=self.config.timeout_seconds,
        ) as sync_client:
            response = sync_client.post(
                f"{self.config.issuer}/oauth/token",
                content=str(
                    httpx.QueryParams(
                        {
                            "client_id": self.config.client_id,
                            "grant_type": "refresh_token",
                            "refresh_token": self._state.refresh_token,
                        },
                    ),
                ),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        return self._accept_refresh(response)

    def _accept_refresh(self, response: httpx.Response) -> CodexAuthState:
        if response.status_code in {400, 401, 403}:
            try:
                body = response.json()
            except ValueError:
                body = None
            error = body.get("error") if isinstance(body, dict) else None
            code = error.get("code") if isinstance(error, dict) else error
            reasons: dict[str, Literal["expired", "reused", "invalidated", "rejected"]] = {
                "refresh_token_expired": "expired",
                "refresh_token_reused": "reused",
                "refresh_token_invalidated": "invalidated",
            }
            raise CodexAuthRefreshError(
                reasons.get(code if isinstance(code, str) else "", "rejected")
            )
        response.raise_for_status()
        payload = _response_mapping(response)
        access_token = _string_value(payload, "access_token")
        account_id = _extract_account_id(
            access_token=access_token or "",
            account_id=_string_value(payload, "account_id"),
            id_token=_string_value(payload, "id_token"),
        )
        if account_id is not None:
            self._guard_account(account_id)
        refreshed_state = CodexAuthState.from_json_dict(
            {
                "OPENAI_API_KEY": self._state.openai_api_key,
                "auth_mode": self._state.auth_mode,
                "last_refresh": _now_utc().isoformat().replace("+00:00", "Z"),
                "tokens": {
                    "access_token": access_token,
                    "account_id": _string_value(payload, "account_id") or self._state.account_id,
                    "id_token": _string_value(payload, "id_token") or self._state.id_token,
                    "refresh_token": _string_value(payload, "refresh_token")
                    or self._state.refresh_token,
                },
            },
        )
        latest = self.store.read_state()
        self._guard_account(latest.account_id)
        if latest.refresh_token != self._state.refresh_token:
            return latest
        self.store.write_state(refreshed_state)
        return refreshed_state
