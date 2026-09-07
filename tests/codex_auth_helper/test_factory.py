from __future__ import annotations as _annotations

import asyncio
import builtins
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import httpx2
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

from .support import write_auth_file


def _config(auth_path: Path) -> CodexAuthConfig:
    return CodexAuthConfig(auth_path=auth_path)


def test_create_codex_responses_model_returns_openai_responses_model(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    model = create_codex_responses_model(
        "gpt-5",
        config=_config(auth_path),
        instructions="Answer tersely.",
    )

    assert isinstance(model, OpenAIResponsesModel)
    assert isinstance(model, CodexResponsesModel)
    assert isinstance(model.client, CodexAsyncOpenAI)
    assert str(model.client.base_url) == "https://chatgpt.com/backend-api/codex/"
    assert model.client.token_manager.current_account_id == "acct_demo"
    assert model.settings == {"openai_store": False}
    assert model.responses_connection == "http"
    assert model.responses_fallback == "error"


def test_create_codex_responses_model_merges_settings(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    model = create_codex_responses_model(
        "gpt-5",
        config=_config(auth_path),
        instructions="Answer tersely.",
        settings={
            "openai_reasoning_summary": "concise",
            "openai_store": True,
        },
    )

    assert model.settings == {
        "openai_reasoning_summary": "concise",
        "openai_store": False,
    }


def test_create_codex_responses_model_selects_responses_lite(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    model = create_codex_responses_model(
        "gpt-5",
        config=_config(auth_path),
        instructions="Answer tersely.",
        transport="responses_lite",
    )

    client = cast("CodexAsyncOpenAI", model.client)
    assert client.responses_transport == "responses_lite"
    assert client.default_headers["x-openai-internal-codex-responses-lite"] == "true"


def test_create_codex_responses_model_selects_websocket(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    def observer(_event: object) -> None:
        return None

    model = create_codex_responses_model(
        "gpt-5",
        config=_config(auth_path),
        instructions="Answer tersely.",
        connection="websocket",
        fallback="http",
        transport_observer=observer,
    )

    assert model.responses_connection == "websocket"
    assert model.responses_fallback == "http"
    client = cast(CodexAsyncOpenAI, model.client)
    assert client.responses_transport_observer is observer


def test_responses_websocket_uses_codex_base_url(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")
    client = create_codex_async_openai(config=_config(auth_path))

    manager = cast(Any, client._http_responses.connect())
    assert str(manager._prepare_url()) == ("wss://chatgpt.com/backend-api/codex/responses")

    asyncio.run(client.close())


def test_create_codex_responses_model_rejects_lite_websocket(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    with pytest.raises(ValueError, match="responses_lite"):
        create_codex_responses_model(
            "gpt-5",
            config=_config(auth_path),
            instructions="Answer tersely.",
            transport="responses_lite",
            connection="websocket",
        )


def test_create_codex_responses_model_rejects_missing_instructions_runtime(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")

    with pytest.raises(ValueError, match="`instructions` is required"):
        create_codex_responses_model(
            "gpt-5",
            config=_config(auth_path),
            instructions=cast("Any", None),
        )


@pytest.mark.asyncio
async def test_codex_responses_model_forces_streaming_on_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_demo")
    model = create_codex_responses_model(
        "gpt-5",
        config=_config(auth_path),
        instructions="Answer tersely.",
    )
    expected_response = ModelResponse(parts=[TextPart("ok")], model_name="gpt-5")
    stream_calls: list[tuple[list[Any], Any, ModelRequestParameters]] = []

    class FakeProcessedResponse:
        def __aiter__(self) -> Any:
            return self._iterator()

        async def _iterator(self) -> Any:
            yield object()

        def get(self) -> ModelResponse:
            return expected_response

    @asynccontextmanager
    async def fake_request_stream(
        messages: list[Any],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
    ) -> AsyncIterator[FakeProcessedResponse]:
        stream_calls.append((messages, model_settings, model_request_parameters))
        yield FakeProcessedResponse()

    monkeypatch.setattr(model, "request_stream", fake_request_stream)

    parameters = ModelRequestParameters()
    response = await model.request([], None, parameters)

    assert len(stream_calls) == 1
    forwarded_messages, forwarded_settings, forwarded_parameters = stream_calls[0]
    assert forwarded_messages == []
    assert forwarded_settings is None
    assert forwarded_parameters is not parameters
    assert forwarded_parameters.instruction_parts is not None
    assert [part.content for part in forwarded_parameters.instruction_parts] == ["Answer tersely."]
    assert parameters.instruction_parts is None
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
    )

    assert client.default_headers["ChatGPT-Account-Id"] == "acct_header"

    await client.close()


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
async def test_codex_async_openai_keeps_borrowed_sdk_and_auth_clients_open(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_borrowed")
    sdk_http_client = httpx2.AsyncClient()
    auth_http_client = httpx.AsyncClient()
    client = create_codex_async_openai(
        config=_config(auth_path),
        http_client=sdk_http_client,
        auth_http_client=auth_http_client,
    )

    await client.close()

    assert sdk_http_client.is_closed is False
    assert auth_http_client.is_closed is False
    await sdk_http_client.aclose()
    await auth_http_client.aclose()


@pytest.mark.asyncio
async def test_codex_async_openai_uses_codex_base_url_for_responses_requests(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_header")
    seen_urls: list[httpx2.URL] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_urls.append(request.url)
        return httpx2.Response(status_code=200, json={"ok": True})

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = create_codex_async_openai(
        config=_config(auth_path),
        http_client=http_client,
    )

    await client.responses.create(
        model="gpt-5",
        input="hello",
        store=False,
        stream=False,
    )

    assert seen_urls == [httpx2.URL("https://chatgpt.com/backend-api/codex/responses")]

    await client.close()
    await http_client.aclose()


@pytest.mark.asyncio
async def test_responses_transport_keeps_standard_tool_request(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_header")
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status_code=200, json={}, request=request)

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = create_codex_async_openai(
        config=_config(auth_path),
        http_client=http_client,
    )

    await client.with_raw_response.responses.create(
        model="gpt-5.6-luna",
        input="Call alpha and beta.",
        instructions="Use both tools.",
        tools=cast(
            "Any",
            [
                {
                    "type": "function",
                    "name": "alpha",
                    "description": "Return alpha.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            ],
        ),
        parallel_tool_calls=True,
        store=False,
    )

    body = json.loads(requests[0].content)
    assert "x-openai-internal-codex-responses-lite" not in requests[0].headers
    assert body["instructions"] == "Use both tools."
    assert body["tools"][0]["name"] == "alpha"
    assert body["parallel_tool_calls"] is True
    assert body["input"] == "Call alpha and beta."

    await http_client.aclose()


@pytest.mark.asyncio
async def test_responses_lite_rewrites_request_and_disables_parallel_tools(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_header")
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status_code=200, json={}, request=request)

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = create_codex_async_openai(
        config=_config(auth_path),
        http_client=http_client,
        transport="responses_lite",
    )

    for _ in range(2):
        await client.with_raw_response.responses.create(
            model="gpt-5.6-luna",
            input="Call both tools.",
            instructions="Use the tools.",
            tools=cast(
                "Any",
                [
                    {
                        "type": "function",
                        "name": "alpha",
                        "description": "Return alpha.",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                        "strict": True,
                    },
                    {
                        "type": "function",
                        "name": "beta",
                        "description": "Return beta.",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                        "strict": True,
                    },
                ],
            ),
            parallel_tool_calls=True,
            reasoning={"effort": "high"},
            store=False,
            extra_body={"client_metadata": {"source": "test"}},
        )

    assert len(requests) == 2
    bodies = [json.loads(request.content) for request in requests]
    first = bodies[0]
    assert requests[0].headers["x-openai-internal-codex-responses-lite"] == "true"
    assert "instructions" not in first
    assert "tools" not in first
    assert first["parallel_tool_calls"] is False
    assert first["reasoning"] == {"effort": "high", "context": "all_turns"}
    assert first["client_metadata"] == {"source": "test"}
    assert first["input"][0]["type"] == "additional_tools"
    assert first["input"][0]["role"] == "developer"
    namespace = first["input"][0]["tools"][0]
    assert namespace["type"] == "namespace"
    assert namespace["name"] == "functions"
    assert namespace["description"] == ""
    assert [tool["name"] for tool in namespace["tools"]] == [
        "alpha",
        "beta",
    ]
    assert first["input"][1]["type"] == "message"
    assert first["input"][1]["role"] == "developer"
    assert first["input"][1]["content"] == [
        {"type": "input_text", "text": "Use the tools."},
    ]
    assert first["input"][2] == {"role": "user", "content": "Call both tools."}
    assert first["input"][0]["id"] == bodies[1]["input"][0]["id"]
    assert first["input"][1]["id"] == bodies[1]["input"][1]["id"]

    await http_client.aclose()


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
        instructions="Answer tersely.",
        reasoning={"effort": "medium"},
        transport="responses_lite",
    )

    assert isinstance(model, ChatOpenAI)
    assert isinstance(model.root_async_client, CodexAsyncOpenAI)
    assert isinstance(model.root_client, CodexOpenAI)
    assert model.use_responses_api is True
    assert model.output_version == "responses/v1"
    assert model.use_previous_response_id is False
    assert model.streaming is True
    assert model.reasoning == {"effort": "medium"}
    assert model.model_kwargs["instructions"] == "Answer tersely."
    assert model.store is False
    assert model.root_async_client.token_manager.current_account_id == "acct_langchain"
    assert model.root_async_client.responses_transport == "responses_lite"
    assert model.root_client.responses_transport == "responses_lite"

    model.root_client.close()
    asyncio.run(model.root_async_client.close())


def test_create_codex_chat_openai_rejects_duplicate_instructions(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_langchain")

    with pytest.raises(ValueError, match="dedicated parameter"):
        create_codex_chat_openai(
            "gpt-5",
            config=_config(auth_path),
            instructions="Answer tersely.",
            model_kwargs={"instructions": "Be concise."},
        )


def test_create_codex_chat_openai_rejects_store_override(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_langchain")

    with pytest.raises(ValueError, match="always forces `store=False`"):
        create_codex_chat_openai(
            "gpt-5",
            config=_config(auth_path),
            instructions="Answer tersely.",
            model_kwargs={"store": True},
        )


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
        if name == "langchain_openai" or (level == 1 and name == "langchain"):
            raise ModuleNotFoundError("No module named 'langchain_openai'")
        return original_import(name, globals, locals, fromlist, level)

    assert fake_import("math").__name__ == "math"
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ModuleNotFoundError, match="codex-auth-helper\\[langchain\\]"):
        create_codex_chat_openai("gpt-5", instructions="Answer tersely.")


def test_create_codex_chat_openai_rejects_missing_instructions_runtime(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_langchain")

    with pytest.raises(ValueError, match="`instructions` is required"):
        create_codex_chat_openai(
            "gpt-5",
            config=_config(auth_path),
            instructions=cast("Any", None),
        )


@pytest.mark.asyncio
async def test_codex_openai_close_schedules_token_cleanup_in_running_loop(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="acct_sync")
    sync_http_client = httpx2.Client()
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
    sync_http_client = httpx2.Client()
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
