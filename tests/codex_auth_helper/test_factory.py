from __future__ import annotations as _annotations

import asyncio
import builtins
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from codex_auth_helper import (
    CodexAsyncOpenAI,
    CodexAuthConfig,
    CodexAuthStore,
    CodexOpenAI,
    CodexResponsesModel,
    CodexTokenManager,
    create_codex_async_openai,
    create_codex_chat_openai,
    create_codex_openai,
    create_codex_responses_model,
)
from langchain_openai import ChatOpenAI
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from typing_extensions import Sentinel

from .support import write_auth_file

_STREAM_EVENT = Sentinel("_STREAM_EVENT")


def _config(auth_path: Path) -> CodexAuthConfig:
    return CodexAuthConfig(auth_path=auth_path)


def test_create_codex_responses_model_returns_openai_responses_model(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    model = create_codex_responses_model("gpt-5", config=_config(auth_path))

    assert isinstance(model, OpenAIResponsesModel)
    assert isinstance(model, CodexResponsesModel)
    assert isinstance(model.client, CodexAsyncOpenAI)
    assert str(model.client.base_url) == "https://chatgpt.com/backend-api/codex/"
    assert model.client.token_manager.current_account_id == "acct_demo"
    assert model.settings == {"openai_store": False}


def test_create_codex_responses_model_merges_settings(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    model = create_codex_responses_model(
        "gpt-5",
        config=_config(auth_path),
        settings={"openai_reasoning_summary": "concise"},
    )

    assert model.settings == {
        "openai_reasoning_summary": "concise",
        "openai_store": False,
    }


@pytest.mark.asyncio
async def test_codex_responses_model_forces_streaming_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")
    model = create_codex_responses_model("gpt-5", config=_config(auth_path))
    expected_response = ModelResponse(parts=[TextPart("ok")], model_name="gpt-5")
    seen_stream_values: list[bool] = []

    class FakeRawResponse:
        async def __aenter__(self) -> FakeRawResponse:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: Any,
        ) -> None:
            del exc_type, exc, traceback

    class FakeProcessedResponse:
        def __aiter__(self) -> Any:
            return self._iterator()

        async def _iterator(self) -> Any:
            yield _STREAM_EVENT

        def get(self) -> ModelResponse:
            return expected_response

    async def fake_responses_create(
        messages: list[Any],
        stream: bool,
        model_settings: dict[str, Any],
        model_request_parameters: ModelRequestParameters,
    ) -> FakeRawResponse:
        del messages, model_settings, model_request_parameters
        seen_stream_values.append(stream)
        return FakeRawResponse()

    async def fake_process_streamed_response(
        response: FakeRawResponse,
        model_settings: dict[str, Any],
        model_request_parameters: ModelRequestParameters,
    ) -> FakeProcessedResponse:
        del response, model_settings, model_request_parameters
        return FakeProcessedResponse()

    monkeypatch.setattr(model, "_responses_create", fake_responses_create)
    monkeypatch.setattr(model, "_process_streamed_response", fake_process_streamed_response)

    response = await model.request([], None, ModelRequestParameters())

    assert seen_stream_values == [True]
    assert response is expected_response


@pytest.mark.asyncio
async def test_codex_token_manager_refreshes_expired_tokens(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(
        auth_path,
        access_expiry=datetime.now(tz=UTC) - timedelta(minutes=5),
        account_id="acct_before",
        refresh_token="refresh_before",
    )

    refreshed_access = "refreshed_access"
    refreshed_refresh = "refreshed_refresh"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code=200,
            json={
                "access_token": refreshed_access,
                "id_token": None,
                "refresh_token": refreshed_refresh,
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    token_manager = CodexTokenManager(
        config=_config(auth_path),
        store=CodexAuthStore(auth_path),
        http_client=http_client,
    )

    token = await token_manager.get_access_token()

    assert token == refreshed_access
    assert len(requests) == 1
    assert requests[0].url == httpx.URL("https://auth.openai.com/oauth/token")
    assert requests[0].headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert requests[0].content.decode("utf-8") == (
        "client_id=app_EMoamEEZ73f0CkXaXp7hrann&"
        "grant_type=refresh_token&refresh_token=refresh_before"
    )

    persisted_state = CodexAuthStore(auth_path).read_state()
    assert persisted_state.access_token == refreshed_access
    assert persisted_state.refresh_token == refreshed_refresh
    assert persisted_state.account_id == "acct_before"

    await http_client.aclose()


@pytest.mark.asyncio
async def test_codex_async_openai_adds_chatgpt_account_header(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_header")
    client = create_codex_async_openai(
        config=_config(auth_path),
        http_client=httpx.AsyncClient(),
    )

    assert client.default_headers["ChatGPT-Account-Id"] == "acct_header"

    await client.token_manager.http_client.aclose()


@pytest.mark.asyncio
async def test_codex_async_openai_covers_missing_account_header_and_owned_close(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="")
    client = create_codex_async_openai(config=_config(auth_path))

    assert "ChatGPT-Account-Id" not in client.default_headers
    assert client.default_headers["originator"] == "codex-auth-helper"
    assert client.token_manager.owns_http_client is True

    await client.close()
    assert client.token_manager.http_client.is_closed is True


@pytest.mark.asyncio
async def test_codex_async_openai_uses_codex_base_url_for_responses_requests(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_header")
    seen_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(request.url)
        return httpx.Response(status_code=200, json={"ok": True})

    client = create_codex_async_openai(
        config=_config(auth_path),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    await client.responses.create(
        model="gpt-5",
        input="hello",
        store=False,
        stream=False,
    )

    assert seen_urls == [httpx.URL("https://chatgpt.com/backend-api/codex/responses")]

    await client.close()


def test_codex_auth_store_missing_file_message(tmp_path: Path) -> None:
    auth_path = tmp_path / "missing-auth.json"
    store = CodexAuthStore(auth_path)

    with pytest.raises(FileNotFoundError, match="Codex auth file was not found"):
        store.read_state()


def test_create_codex_openai_returns_sync_openai_client(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_sync")

    client = create_codex_openai(config=_config(auth_path))

    assert isinstance(client, CodexOpenAI)
    assert client.default_headers["ChatGPT-Account-Id"] == "acct_sync"
    assert client.default_headers["originator"] == "codex-auth-helper"
    assert str(client.base_url) == "https://chatgpt.com/backend-api/codex/"

    client.close()


def test_create_codex_chat_openai_returns_langchain_chat_model(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_langchain")

    model = create_codex_chat_openai(
        "gpt-5",
        config=_config(auth_path),
        reasoning={"effort": "medium"},
        use_previous_response_id=True,
    )

    assert isinstance(model, ChatOpenAI)
    assert isinstance(model.root_async_client, CodexAsyncOpenAI)
    assert isinstance(model.root_client, CodexOpenAI)
    assert model.use_responses_api is True
    assert model.output_version == "responses/v1"
    assert model.use_previous_response_id is True
    assert model.reasoning == {"effort": "medium"}
    assert model.root_async_client.token_manager.current_account_id == "acct_langchain"

    model.root_client.close()
    asyncio.run(model.root_async_client.close())


def test_create_codex_chat_openai_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "langchain_openai":
            raise ModuleNotFoundError("No module named 'langchain_openai'")
        return original_import(name, globals, locals, fromlist, level)

    assert fake_import("math").__name__ == "math"
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ModuleNotFoundError, match="codex-auth-helper\\[langchain\\]"):
        create_codex_chat_openai("gpt-5")


@pytest.mark.asyncio
async def test_codex_openai_close_schedules_token_cleanup_in_running_loop(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_sync")
    sync_http_client = httpx.Client()
    client = create_codex_openai(
        config=_config(auth_path),
        http_client=sync_http_client,
    )

    client.close()
    await asyncio.sleep(0)

    assert sync_http_client.is_closed is False
    assert client.token_manager.http_client.is_closed is True
    sync_http_client.close()


def test_codex_openai_close_skips_async_cleanup_when_token_manager_is_not_owner(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_manual")
    async_http_client = httpx.AsyncClient()
    sync_http_client = httpx.Client()
    token_manager = CodexTokenManager(
        config=_config(auth_path),
        store=CodexAuthStore(auth_path),
        http_client=async_http_client,
        owns_http_client=False,
    )
    client = CodexOpenAI(
        base_url="https://chatgpt.com/backend-api/codex",
        http_client=sync_http_client,
        token_manager=token_manager,
        owns_http_client=False,
    )

    client.close()

    assert sync_http_client.is_closed is False
    assert async_http_client.is_closed is False
    sync_http_client.close()
    asyncio.run(async_http_client.aclose())
