from __future__ import annotations as _annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import codex_auth_helper.auth.manager as manager_module
import httpx
import httpx2
import pytest
from codex_auth_helper import (
    CodexAuthAccountMismatchError,
    CodexAuthConfig,
    CodexAuthRefreshError,
    CodexAuthStore,
    CodexResponsesConnectionError,
    CodexTokenManager,
    create_codex_async_openai,
    create_codex_openai,
)
from openai import AuthenticationError

from .support import _jwt, write_auth_file
from .test_websocket import _FakeResponses, _text_events

FUTURE = datetime(2099, 1, 1, tzinfo=UTC)
PAST = datetime(2000, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("sync", [False, True])
async def test_refresh_without_new_id_token_does_not_refresh_forever(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sync: bool,
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=PAST)
    calls: list[int] = []

    def refresh(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={k: v for k, v in _payload().items() if k != "id_token"})

    original_client = httpx.Client
    monkeypatch.setattr(
        manager_module.httpx,
        "Client",
        lambda **kw: original_client(
            **kw,
            transport=httpx.MockTransport(refresh),
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as http:
        manager = CodexTokenManager(CodexAuthConfig(auth_path=auth), CodexAuthStore(auth), http)
        if sync:
            assert (
                manager.recover_after_unauthorized_sync(
                    manager.current_state.access_token, refresh=False
                )
                is None
            )
            assert (
                manager.recover_after_unauthorized_sync(
                    manager.current_state.access_token, refresh=True
                )
                == _payload()["access_token"]
            )
            token = manager.get_access_token_sync()
        else:
            await manager.get_access_token()
            token = await manager.get_access_token()
        assert token == _payload()["access_token"]
        assert manager.store.read_state().expires_at == FUTURE
    assert len(calls) == 1


async def test_cancelled_refresh_releases_shared_lock(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=PAST)
    started = asyncio.Event()
    calls: list[int] = []

    async def refresh(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as http:
        config, store = CodexAuthConfig(auth_path=auth), CodexAuthStore(auth)
        first = CodexTokenManager(config, store, http)
        second = CodexTokenManager(config, store, http)
        task = asyncio.create_task(first.get_access_token())
        try:
            await asyncio.wait_for(started.wait(), 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.wait_for(second.get_access_token(), 2) == _payload()["access_token"]


async def test_rotation_during_refresh_preserves_newer_file(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=PAST)
    store = CodexAuthStore(auth)

    def refresh(request: httpx.Request) -> httpx.Response:
        _rotate(store)
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as http:
        manager = CodexTokenManager(CodexAuthConfig(auth_path=auth), store, http)
        assert await manager.get_access_token() == "synthetic-reloaded"
        assert store.read_state().refresh_token == "synthetic-rotated-on-disk"


async def test_cancelled_401_waiter_does_not_take_another_managers_lock(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=FUTURE)
    async with httpx.AsyncClient() as http:
        first = CodexTokenManager(CodexAuthConfig(auth_path=auth), CodexAuthStore(auth), http)
        second = CodexTokenManager(CodexAuthConfig(auth_path=auth), CodexAuthStore(auth), http)
        first._refresh_lock.acquire()
        task = asyncio.create_task(second.recover_after_unauthorized("synthetic", refresh=False))
        try:
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert first._refresh_lock.locked()
        finally:
            first._refresh_lock.release()
        assert (
            await asyncio.wait_for(second.get_access_token(), 1) == first.current_state.access_token
        )


@pytest.mark.parametrize("sync", [False, True])
async def test_non_json_permanent_refresh_error_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sync: bool,
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=PAST)
    before = auth.read_bytes()

    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="synthetic-private-response")

    original_client = httpx.Client
    monkeypatch.setattr(
        manager_module.httpx,
        "Client",
        lambda **kw: original_client(
            **kw,
            transport=httpx.MockTransport(reject),
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as http:
        manager = CodexTokenManager(CodexAuthConfig(auth_path=auth), CodexAuthStore(auth), http)
        with pytest.raises(CodexAuthRefreshError) as caught:
            if sync:
                manager.get_access_token_sync()
            else:
                await manager.get_access_token()
        assert caught.value.reason == "rejected"
        assert "synthetic-private-response" not in str(caught.value)
    assert auth.read_bytes() == before


@pytest.mark.parametrize("kind", ["custom_auth", "other_endpoint", "foreign_origin"])
async def test_401_recovery_is_only_for_managed_responses_requests(
    tmp_path: Path, kind: str
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=FUTURE)
    calls: list[int] = []

    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("unrelated requests must not refresh credentials")

    def reject(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(401, json={})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as auth_http,
        httpx2.AsyncClient(transport=httpx2.MockTransport(reject)) as http,
    ):
        client = create_codex_async_openai(
            config=CodexAuthConfig(auth_path=auth), http_client=http, auth_http_client=auth_http
        )
        try:
            with pytest.raises(AuthenticationError):
                if kind == "custom_auth":
                    await client.responses.create(
                        model="gpt-test",
                        input="x",
                        extra_headers={"Authorization": "Bearer synthetic-custom"},
                    )
                else:
                    path = (
                        "/models" if kind == "other_endpoint" else "https://foreign.test/responses"
                    )
                    await client.get(path, cast_to=object)
        finally:
            await client.close()
    assert len(calls) == 1


def _payload() -> dict[str, str]:
    return {
        "access_token": _jwt({"exp": int(FUTURE.timestamp()), "sub": "synthetic-new"}),
        "id_token": _jwt({"exp": int(FUTURE.timestamp())}),
        "refresh_token": "synthetic-rotated",
    }


def _rotate(store: CodexAuthStore, *, account_id: str | None = None) -> None:
    state = store.read_state()
    store.write_state(
        replace(
            state,
            access_token="synthetic-reloaded",
            refresh_token="synthetic-rotated-on-disk",
            expires_at=FUTURE,
            id_token=_jwt({"exp": int(FUTURE.timestamp())}),
            account_id=account_id or state.account_id,
        )
    )


async def test_independent_managers_refresh_once_and_sync_reader_reloads(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=PAST, refresh_token="synthetic-old")
    calls: list[bytes] = []
    started, release = asyncio.Event(), asyncio.Event()

    async def refresh(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        started.set()
        await release.wait()
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as http:
        managers = [
            CodexTokenManager(CodexAuthConfig(auth_path=auth), CodexAuthStore(auth), http)
            for _ in range(4)
        ]
        tasks = [asyncio.create_task(manager.get_access_token()) for manager in managers[:3]]
        try:
            await asyncio.wait_for(started.wait(), 2)
            sync_task = asyncio.create_task(asyncio.to_thread(managers[3].get_access_token_sync))
            release.set()
            result = await asyncio.wait_for(asyncio.gather(*tasks, sync_task), 3)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert result == [_payload()["access_token"]] * 4
    assert len(calls) == 1
    assert b"refresh_token=synthetic-old" in calls[0]
    assert CodexAuthStore(auth).read_state().refresh_token == "synthetic-rotated"


@pytest.mark.parametrize("sync", [False, True])
async def test_stale_manager_refuses_changed_account_without_refresh(
    tmp_path: Path, sync: bool
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=PAST)

    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("account mismatch must fail before refresh")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        manager = CodexTokenManager(CodexAuthConfig(auth_path=auth), CodexAuthStore(auth), http)
        original = manager.current_state
        _rotate(manager.store, account_id="different-account")
        with pytest.raises(CodexAuthAccountMismatchError, match="changed account"):
            if sync:
                manager.get_access_token_sync()
            else:
                await manager.get_access_token()
        assert manager.current_state == original
        assert manager.store.read_state().account_id == "different-account"


@pytest.mark.parametrize(
    "rotate,always_reject,expected_requests,expected_refreshes",
    [
        (True, False, 2, 0),
        (False, False, 2, 1),
        (False, True, 2, 1),
        (True, True, 3, 1),
    ],
)
async def test_http_401_reload_then_refresh_is_bounded(
    tmp_path: Path,
    rotate: bool,
    always_reject: bool,
    expected_requests: int,
    expected_refreshes: int,
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=FUTURE)
    store = CodexAuthStore(auth)
    initial = store.read_state().access_token
    seen: list[tuple[str, str]] = []
    refreshes: list[bytes] = []

    def refresh(request: httpx.Request) -> httpx.Response:
        refreshes.append(request.content)
        return httpx.Response(200, json=_payload())

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.headers["authorization"], request.headers["ChatGPT-Account-Id"]))
        if rotate and len(seen) == 1:
            _rotate(store)
        rejected = always_reject or request.headers["authorization"] == f"Bearer {initial}"
        return httpx2.Response(401 if rejected else 200, json={})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as auth_http,
        httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http,
    ):
        client = create_codex_async_openai(
            config=CodexAuthConfig(auth_path=auth),
            http_client=http,
            auth_http_client=auth_http,
        )
        try:
            if always_reject:
                with pytest.raises(AuthenticationError):
                    await client.responses.create(model="gpt-test", input="synthetic")
            else:
                await client.responses.create(model="gpt-test", input="synthetic")
        finally:
            await client.close()
    assert len(seen) == expected_requests
    assert len(refreshes) == expected_refreshes
    assert {account for _, account in seen} == {"acct_default"}
    assert seen[-1][0] != seen[0][0]


def test_sync_http_401_reload_uses_updated_file_without_refresh(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=FUTURE)
    seen: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            _rotate(CodexAuthStore(auth))
            return httpx2.Response(401, json={})
        return httpx2.Response(200, json={})

    with httpx2.Client(transport=httpx2.MockTransport(respond)) as http:
        client = create_codex_openai(config=CodexAuthConfig(auth_path=auth), http_client=http)
        try:
            client.responses.create(model="gpt-test", input="synthetic")
        finally:
            client.close()
    assert len(seen) == 2
    assert seen[-1] == "Bearer synthetic-reloaded"


@pytest.mark.parametrize(
    "code,reason",
    [
        ("refresh_token_expired", "expired"),
        ("refresh_token_reused", "reused"),
        ("refresh_token_invalidated", "invalidated"),
        ("invalid_grant", "rejected"),
    ],
)
async def test_permanent_refresh_failure_is_not_retried_by_sdk_or_exposed(
    tmp_path: Path,
    code: str,
    reason: str,
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=FUTURE)
    before = auth.read_bytes()
    refresh_calls: list[int] = []
    request_calls: list[int] = []

    def refresh(request: httpx.Request) -> httpx.Response:
        refresh_calls.append(1)
        return httpx.Response(401, json={"error": {"code": code, "message": "synthetic-private"}})

    def respond(request: httpx2.Request) -> httpx2.Response:
        request_calls.append(1)
        return httpx2.Response(401, json={})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as auth_http,
        httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http,
    ):
        client = create_codex_async_openai(
            config=CodexAuthConfig(auth_path=auth),
            http_client=http,
            auth_http_client=auth_http,
        )
        try:
            with pytest.raises(CodexAuthRefreshError) as caught:
                await client.responses.create(model="gpt-test", input="synthetic")
            assert caught.value.reason == reason
            assert "synthetic-private" not in str(caught.value)
        finally:
            await client.close()
    assert len(refresh_calls) == len(request_calls) == 1
    assert auth.read_bytes() == before


@pytest.mark.parametrize("change_during_refresh", [False, True])
async def test_refresh_cannot_switch_account_or_overwrite_new_login(
    tmp_path: Path,
    change_during_refresh: bool,
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=PAST)
    store = CodexAuthStore(auth)
    before = auth.read_bytes()

    def refresh(request: httpx.Request) -> httpx.Response:
        if change_during_refresh:
            _rotate(store, account_id="different-account")
            return httpx.Response(200, json=_payload())
        return httpx.Response(200, json={**_payload(), "account_id": "different-account"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as http:
        manager = CodexTokenManager(CodexAuthConfig(auth_path=auth), store, http)
        with pytest.raises(CodexAuthAccountMismatchError):
            await manager.get_access_token()
    if change_during_refresh:
        assert store.read_state().account_id == "different-account"
    else:
        assert auth.read_bytes() == before


@pytest.mark.parametrize("always_reject", [False, True])
async def test_websocket_handshake_401_reload_is_bounded_and_never_falls_back(
    tmp_path: Path,
    always_reject: bool,
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth, access_expiry=FUTURE)
    attempts: list[str] = []
    refreshes: list[int] = []
    resource = _FakeResponses(event_batches=[_text_events("resp_ok", "ok")])

    class UnauthorizedError(Exception):
        status_code = 401

    class Handshake:
        def __init__(self, delegate: Any, rejected: bool) -> None:
            self.delegate, self.rejected = delegate, rejected

        async def __aenter__(self) -> Any:
            if self.rejected:
                raise UnauthorizedError()
            return await self.delegate.__aenter__()

        async def __aexit__(self, *args: Any) -> None:
            await self.delegate.__aexit__(*args)

    def connect(**kwargs: Any) -> Handshake:
        attempts.append(kwargs["extra_headers"]["Authorization"])
        if len(attempts) == 1:
            _rotate(CodexAuthStore(auth))
        return Handshake(resource.connect(**kwargs), always_reject or len(attempts) == 1)

    def refresh(request: httpx.Request) -> httpx.Response:
        refreshes.append(1)
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(refresh)) as auth_http:
        client = create_codex_async_openai(
            config=CodexAuthConfig(auth_path=auth), auth_http_client=auth_http
        )
        client._http_responses = cast(Any, SimpleNamespace(connect=connect))

        try:
            async with client.responses_session(connection="websocket", fallback="http") as session:
                if always_reject:
                    with pytest.raises(CodexResponsesConnectionError):
                        await client.responses.create(model="gpt-test", input="x", stream=True)
                else:
                    stream = await client.responses.create(model="gpt-test", input="x", stream=True)
                    async with stream:
                        async for _ in stream:
                            pass
                assert session.fallback_used is False
        finally:
            await client.close()
    assert len(attempts) == (3 if always_reject else 2)
    assert len(refreshes) == (1 if always_reject else 0)
    assert attempts[0] != attempts[1]
