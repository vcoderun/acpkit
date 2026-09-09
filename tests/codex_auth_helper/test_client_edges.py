from __future__ import annotations as _annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import pytest
from codex_auth_helper import (
    CodexOpenAI,
    CodexResponsesProtocolError,
    CodexResponsesSessionInfo,
)
from codex_auth_helper import client as helper_client
from openai import AsyncOpenAI, AuthenticationError, OpenAI
from openai._models import FinalRequestOptions

from .test_websocket import _client, _FakeConnection, _FakeResponses


class _FailingSessionManager:
    async def __aexit__(self, *_exc_info: object) -> None:
        raise RuntimeError("session close failed")


class _FailingHttpResponses(_FakeResponses):
    async def create(self, **kwargs: Any) -> Any:
        del kwargs
        raise RuntimeError("HTTP response failed")


class _SyncTokenManager:
    current_account_id = "acct_sync"
    owns_http_client = False

    def __init__(self) -> None:
        self.recovery_attempts: list[bool] = []

    def get_access_token_sync(self) -> str:
        return "sync-token"

    def recover_after_unauthorized_sync(self, rejected: str, *, refresh: bool) -> None:
        assert rejected == "rejected"
        self.recovery_attempts.append(refresh)


@pytest.mark.asyncio
async def test_response_session_lifecycle_preserves_primary_failures() -> None:
    direct_connection = _FakeConnection()
    state = helper_client._ResponsesSessionState(
        info=CodexResponsesSessionInfo(requested_connection="websocket"),
        fallback="error",
        connection=direct_connection,
        in_flight=True,
    )
    await state.close_connection()
    assert direct_connection.closed
    assert not state.in_flight

    client, http = _client(_FakeResponses())
    try:
        client.responses_transport = "responses_lite"
        with pytest.raises(ValueError, match="does not support"):
            async with client.responses_session(connection="websocket"):
                pass

        client.responses_transport = "responses"
        async with client.responses_session(connection="http"):
            with pytest.raises(CodexResponsesProtocolError, match="outer connection policy"):
                async with client.responses_session(connection="websocket"):
                    pass

        with pytest.raises(RuntimeError, match="session close failed"):
            async with client.responses_session(connection="http"):
                active = client.current_responses_session()
                assert active is not None
                active.manager = _FailingSessionManager()

        with pytest.raises(ValueError, match="primary failure"):
            async with client.responses_session(connection="http"):
                active = client.current_responses_session()
                assert active is not None
                active.manager = _FailingSessionManager()
                raise ValueError("primary failure")
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_http_failure_and_websocket_cancellation_release_transport_state() -> None:
    events: list[Any] = []
    client, http = _client(_FailingHttpResponses(), observer=events.append)
    try:
        async with client.responses_session(connection="http"):
            with pytest.raises(RuntimeError, match="HTTP response failed"):
                await client.responses.create(model="gpt-test", input="hello")
        assert events[0].phase == "failed"
        assert events[0].failure_category == "http"
    finally:
        await http.aclose()

    resource = _FakeResponses(send_error=cast("Any", asyncio.CancelledError()))
    client, http = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            with pytest.raises(asyncio.CancelledError):
                await client.responses.create(model="gpt-test", input="cancel", stream=True)
            state = client.current_responses_session()
            assert state is not None
            assert not state.in_flight
        assert resource.connections[0].closed
    finally:
        await http.aclose()


def test_client_validation_and_transport_metrics_reject_ambiguous_values() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        helper_client._validate_transport(cast("Any", "unknown"))
    with pytest.raises(CodexResponsesProtocolError, match="extra_headers"):
        helper_client._string_headers(object())
    with pytest.raises(CodexResponsesProtocolError, match="extra_query"):
        helper_client._mapping_or_empty(object())

    headers = {"ChatGPT-Account-Id": "stale"}
    helper_client._set_auth_headers(headers, "fresh", None)
    assert headers == {"Authorization": "Bearer fresh"}

    dumpable = SimpleNamespace(model_dump=lambda *, mode: {"mode": mode})
    assert helper_client._json_bytes(dumpable) == len(b'{"mode":"json"}')
    assert helper_client._json_bytes(object()) is None


@pytest.mark.asyncio
async def test_response_processing_without_turn_state_delegates_to_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        is_success=True,
        request=httpx2.Request("POST", "https://example.test/v1/responses"),
        headers={"x-codex-turn-state": "unused"},
    )

    async def async_process(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return response

    monkeypatch.setattr(AsyncOpenAI, "_process_response", async_process)
    async_client, http = _client(_FakeResponses())
    try:
        assert await async_client._process_response(response=response) is response
        failed_response = SimpleNamespace(
            is_success=False,
            request=response.request,
            headers={},
        )
        assert await async_client._process_response(response=failed_response) is response
    finally:
        await http.aclose()

    def sync_process(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return response

    monkeypatch.setattr(OpenAI, "_process_response", sync_process)
    http_sync = httpx2.Client()
    sync_client = CodexOpenAI(
        base_url="https://example.test/v1/",
        http_client=http_sync,
        token_manager=cast("Any", _SyncTokenManager()),
        owns_http_client=False,
    )
    try:
        assert sync_client._process_response(response=response) is response
        assert sync_client._process_response(response=failed_response) is response
    finally:
        sync_client.close()
        http_sync.close()


def test_sync_client_lite_preparation_and_auth_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_manager = _SyncTokenManager()
    http = httpx2.Client()
    client = CodexOpenAI(
        base_url="https://example.test/v1/",
        http_client=http,
        token_manager=cast("Any", token_manager),
        owns_http_client=False,
        transport="responses_lite",
    )
    try:
        prepared = client._prepare_options(
            FinalRequestOptions(
                method="post",
                url="/responses",
                json_data={"model": "gpt-test", "input": "hello"},
            )
        )
        assert cast("dict[str, Any]", prepared.json_data)["parallel_tool_calls"] is False

        request = httpx2.Request(
            "POST",
            "https://example.test/v1/responses",
            headers={"Authorization": "Bearer rejected"},
        )
        failure = AuthenticationError(
            "rejected",
            response=httpx2.Response(401, request=request),
            body=None,
        )

        def reject(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise failure

        monkeypatch.setattr(OpenAI, "request", reject)
        explicit_options = FinalRequestOptions(
            method="post",
            url="/responses",
            headers={"Authorization": "Bearer explicit"},
        )
        with pytest.raises(AuthenticationError):
            client.request(object, explicit_options)

        retry_options = FinalRequestOptions(method="post", url="/responses")
        with pytest.raises(AuthenticationError):
            client.request(object, retry_options)
        assert token_manager.recovery_attempts == [False, True]
    finally:
        client.close()
        http.close()
