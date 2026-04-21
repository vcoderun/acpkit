from __future__ import annotations as _annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from codex_auth_helper.auth.config import CodexAuthConfig, default_auth_path
from codex_auth_helper.auth.manager import CodexTokenManager, _response_mapping, _string_value
from codex_auth_helper.auth.state import (
    CodexAuthState,
    _as_string_mapping,
    _encode_timestamp,
    _extract_account_id,
    _extract_account_id_from_claims,
    _extract_expiry,
    _optional_str,
    _parse_jwt_claims,
    _parse_timestamp,
    _require_str,
)
from codex_auth_helper.auth.store import CodexAuthStore

from .support import write_auth_file


def test_auth_state_helpers_cover_required_optional_and_mapping_paths() -> None:
    assert _require_str({"token": "value"}, "token") == "value"
    with pytest.raises(ValueError, match="token"):
        _require_str({"token": ""}, "token")

    assert _optional_str({"token": "value"}, "token") == "value"
    assert _optional_str({"token": ""}, "token") is None
    assert _as_string_mapping(None) is None
    assert _as_string_mapping(cast(Any, {"ok": 1, 2: "ignored"})) == {"ok": 1}


def test_auth_state_parses_timestamps_claims_and_account_fallbacks() -> None:
    now = datetime.now(tz=UTC).replace(microsecond=0)
    encoded = _encode_timestamp(now)
    assert encoded is not None
    assert _parse_timestamp(encoded) == now
    assert _encode_timestamp(None) is None
    assert _parse_timestamp(None) is None

    access_token = write_auth_file.__globals__["_jwt"](
        {
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "organizations": [{"id": "org_123"}],
        }
    )
    id_token = write_auth_file.__globals__["_jwt"](
        {
            "exp": int((now + timedelta(hours=2)).timestamp()),
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct_nested"},
        }
    )

    assert _parse_jwt_claims("not-a-jwt") is None
    assert _parse_jwt_claims("a.b.c") is None
    assert (
        _extract_account_id(
            access_token=access_token,
            account_id=None,
            id_token=id_token,
        )
        == "acct_nested"
    )
    assert (
        _extract_account_id(
            access_token=access_token,
            account_id="acct_direct",
            id_token=None,
        )
        == "acct_direct"
    )
    assert (
        _extract_account_id(
            access_token=access_token,
            account_id=None,
            id_token=None,
        )
        == "org_123"
    )
    assert _extract_expiry(access_token=access_token, id_token=id_token) == datetime.fromtimestamp(
        int((now + timedelta(hours=2)).timestamp()),
        tz=UTC,
    )


def test_auth_state_helper_edge_paths_cover_missing_nested_claims() -> None:
    assert _extract_account_id_from_claims({}) is None
    assert (
        _extract_account_id_from_claims({"https://api.openai.com/auth": {"chatgpt_account_id": ""}})
        is None
    )
    assert _extract_account_id_from_claims({"organizations": [{}]}) is None
    assert _extract_account_id_from_claims({"organizations": [{"id": ""}]}) is None
    assert _extract_account_id_from_claims({"organizations": [{"id": 1}]}) is None
    assert _extract_account_id_from_claims({"organizations": ["invalid"]}) is None
    assert (
        _extract_account_id(
            access_token="not-a-jwt",
            account_id=None,
            id_token="also-not-a-jwt",
        )
        is None
    )
    assert _extract_expiry(access_token="not-a-jwt", id_token="also-not-a-jwt") is None
    invalid_exp_token = write_auth_file.__globals__["_jwt"]({"exp": "not-an-int"})
    fallback_exp_token = write_auth_file.__globals__["_jwt"]({"exp": 1})
    assert _extract_expiry(
        access_token=fallback_exp_token, id_token=invalid_exp_token
    ) == datetime.fromtimestamp(
        1,
        tz=UTC,
    )


def test_auth_state_json_round_trip_and_invalid_payloads() -> None:
    now = datetime.now(tz=UTC).replace(microsecond=0)
    access_token = write_auth_file.__globals__["_jwt"](
        {
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "chatgpt_account_id": "acct_direct",
        }
    )
    state = CodexAuthState.from_json_dict(
        {
            "OPENAI_API_KEY": "sk-demo",
            "auth_mode": "oauth",
            "last_refresh": now.isoformat().replace("+00:00", "Z"),
            "tokens": {
                "access_token": access_token,
                "refresh_token": "refresh-token",
            },
        }
    )

    assert state.account_id == "acct_direct"
    assert state.openai_api_key == "sk-demo"
    payload = cast(dict[str, Any], state.to_json_dict())
    tokens = cast(dict[str, Any], payload["tokens"])
    assert tokens["account_id"] == "acct_direct"

    with pytest.raises(ValueError, match="Expected `tokens`"):
        CodexAuthState.from_json_dict({})


def test_auth_store_covers_invalid_json_non_object_and_write_round_trip(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    store = CodexAuthStore(auth_path)
    auth_path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        store.read_state()

    auth_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        store.read_state()

    write_auth_file(auth_path, account_id="acct_store")
    state = store.read_state()
    assert state.account_id == "acct_store"

    updated_state = CodexAuthState(
        access_token=state.access_token,
        refresh_token=state.refresh_token,
        account_id="acct_written",
        auth_mode="oauth",
        last_refresh=datetime.now(tz=UTC),
    )
    store.write_state(updated_state)
    persisted = json.loads(auth_path.read_text(encoding="utf-8"))
    assert persisted["tokens"]["account_id"] == "acct_written"


@pytest.mark.asyncio
async def test_token_manager_helpers_cover_non_refresh_and_close_paths(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_current")
    config = CodexAuthConfig(auth_path=auth_path)
    client = httpx.AsyncClient()
    manager = CodexTokenManager(
        config=config,
        store=CodexAuthStore(auth_path),
        http_client=client,
    )

    assert manager.current_account_id == "acct_current"
    assert manager._refresh_deadline(manager.current_state) is not None
    assert manager._should_refresh(manager.current_state) is False
    assert await manager.get_access_token() == manager.current_state.access_token

    request = httpx.Request("GET", "https://chatgpt.com/backend-api/conversations")
    await manager.prepare_account_header(request)
    assert request.headers["ChatGPT-Account-Id"] == "acct_current"

    foreign_request = httpx.Request("GET", "https://api.example.com/data")
    await manager.prepare_account_header(foreign_request)
    assert "ChatGPT-Account-Id" not in foreign_request.headers

    manager._state = CodexAuthState(
        access_token=manager.current_state.access_token,
        refresh_token=manager.current_state.refresh_token,
        account_id=None,
    )
    chatgpt_request = httpx.Request("GET", "https://chatgpt.com/backend-api/conversations")
    await manager.prepare_account_header(chatgpt_request)
    assert "ChatGPT-Account-Id" not in chatgpt_request.headers

    await manager.close()
    await client.aclose()


def test_token_manager_sync_refresh_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(
        auth_path,
        access_expiry=datetime.now(tz=UTC) - timedelta(minutes=5),
        account_id="acct_before",
        refresh_token="refresh-before",
    )
    manager = CodexTokenManager(
        config=CodexAuthConfig(auth_path=auth_path),
        store=CodexAuthStore(auth_path),
        http_client=httpx.AsyncClient(),
    )
    refreshed_state = CodexAuthState(
        access_token="refreshed-access",
        refresh_token=manager.current_state.refresh_token,
        account_id="acct_before",
    )
    monkeypatch.setattr(
        CodexTokenManager,
        "_refresh_locked_sync",
        lambda self: refreshed_state,
    )

    token = manager.get_access_token_sync()

    assert token == "refreshed-access"
    assert manager.current_state.account_id == "acct_before"
    asyncio.run(manager.close())


def test_token_manager_sync_non_refresh_returns_current_token(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_sync")
    manager = CodexTokenManager(
        config=CodexAuthConfig(auth_path=auth_path),
        store=CodexAuthStore(auth_path),
        http_client=httpx.AsyncClient(),
    )

    token = manager.get_access_token_sync()

    assert token == manager.current_state.access_token
    assert manager.current_state.account_id == "acct_sync"
    asyncio.run(manager.close())


def test_token_manager_sync_refresh_real_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(
        auth_path,
        access_expiry=datetime.now(tz=UTC) - timedelta(minutes=5),
        account_id="acct_before",
        refresh_token="refresh_before",
    )
    seen_calls: list[tuple[str, str, dict[str, str]]] = []

    class FakeSyncClient:
        def __init__(self, *, follow_redirects: bool, timeout: float) -> None:
            assert follow_redirects is True
            assert timeout > 0

        def __enter__(self) -> FakeSyncClient:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: Any,
        ) -> None:
            del exc_type, exc, traceback

        def post(self, url: str, *, content: str, headers: dict[str, str]) -> httpx.Response:
            seen_calls.append((url, content, headers))
            return httpx.Response(
                status_code=200,
                request=httpx.Request("POST", url, content=content, headers=headers),
                json={
                    "access_token": "sync_refreshed_access",
                    "refresh_token": "sync_refreshed_refresh",
                },
            )

    monkeypatch.setattr(httpx, "Client", FakeSyncClient)
    manager = CodexTokenManager(
        config=CodexAuthConfig(auth_path=auth_path),
        store=CodexAuthStore(auth_path),
        http_client=httpx.AsyncClient(),
    )

    token = manager.get_access_token_sync()

    assert token == "sync_refreshed_access"
    assert seen_calls == [
        (
            "https://auth.openai.com/oauth/token",
            "client_id=app_EMoamEEZ73f0CkXaXp7hrann&"
            "grant_type=refresh_token&refresh_token=refresh_before",
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
    ]
    persisted_state = CodexAuthStore(auth_path).read_state()
    assert persisted_state.access_token == "sync_refreshed_access"
    assert persisted_state.refresh_token == "sync_refreshed_refresh"
    assert persisted_state.account_id == "acct_before"
    asyncio.run(manager.close())


@pytest.mark.asyncio
async def test_token_manager_refresh_fallbacks_and_response_validation(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(
        auth_path,
        access_expiry=datetime.now(tz=UTC) - timedelta(minutes=5),
        account_id="acct_before",
        refresh_token="refresh-before",
    )

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "refreshed-access",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = CodexTokenManager(
        config=CodexAuthConfig(auth_path=auth_path),
        store=CodexAuthStore(auth_path),
        http_client=client,
    )

    token = await manager.get_access_token()

    assert token == "refreshed-access"
    assert len(calls) == 1
    assert manager.current_state.refresh_token == "refresh-before"
    assert manager.current_state.account_id == "acct_before"

    assert _string_value({"token": "value"}, "token") == "value"
    assert _string_value({"token": ""}, "token") is None
    with pytest.raises(ValueError, match="return an object"):
        _response_mapping(httpx.Response(200, json=[]))

    owner_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    owner_manager = CodexTokenManager(
        config=CodexAuthConfig(auth_path=auth_path),
        store=CodexAuthStore(auth_path),
        http_client=owner_client,
        owns_http_client=True,
    )
    await owner_manager.close()
    assert owner_client.is_closed is True

    await client.aclose()


def test_auth_config_default_path_and_refresh_deadline_without_expiry(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_deadline")
    config = CodexAuthConfig(auth_path=auth_path)
    assert default_auth_path().name == "auth.json"

    state = CodexAuthState(
        access_token="access",
        refresh_token="refresh",
        last_refresh=datetime.now(tz=UTC),
    )
    client = httpx.AsyncClient()
    manager = CodexTokenManager(
        config=config,
        store=CodexAuthStore(auth_path),
        http_client=client,
    )

    try:
        manager._state = state
        deadline = manager._refresh_deadline(state)
        assert state.last_refresh is not None
        assert deadline == state.last_refresh + config.default_token_ttl

        missing_deadline_state = CodexAuthState(
            access_token="access",
            refresh_token="refresh",
        )
        assert manager._refresh_deadline(missing_deadline_state) is None
        assert manager._should_refresh(missing_deadline_state) is False
    finally:
        asyncio.run(client.aclose())
