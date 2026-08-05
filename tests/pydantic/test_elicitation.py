from __future__ import annotations as _annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AcceptElicitationResponse,
    CancelElicitationResponse,
    ClientCapabilities,
    CreateElicitationResponse,
    DeclineElicitationResponse,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    ElicitationFormSessionMode,
    ElicitationMode,
    ElicitationStringPropertySchema,
)
from pydantic_acp import (
    AcpSessionContext,
    ChoiceElicitationAccepted,
    ChoiceElicitationCancelled,
    ChoiceElicitationDeclined,
    ElicitationChoice,
    ElicitationUnsupportedError,
    InvalidElicitationResponseError,
)
from pydantic_acp.elicitation import _parse_choice_response


@dataclass(slots=True)
class _ElicitationClient:
    responses: list[CreateElicitationResponse] = field(default_factory=list)
    calls: list[tuple[str, ElicitationMode]] = field(default_factory=list)

    async def create_elicitation(
        self,
        message: str,
        mode: ElicitationMode,
        **kwargs: Any,
    ) -> CreateElicitationResponse:
        del kwargs
        self.calls.append((message, mode))
        if not self.responses:
            raise AssertionError("unexpected elicitation request")
        return self.responses.pop(0)


def _session(
    client: _ElicitationClient,
    *,
    supports_form: bool = True,
) -> AcpSessionContext:
    capabilities = (
        ClientCapabilities(
            elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities()),
        )
        if supports_form
        else ClientCapabilities()
    )
    now = datetime.now(UTC)
    return AcpSessionContext(
        session_id="elicitation-session",
        cwd=Path("/tmp"),
        created_at=now,
        updated_at=now,
        client=cast("AcpClient", client),
        client_capabilities=capabilities,
    )


def test_ask_choice_returns_typed_value_and_compiles_form_schema() -> None:
    client = _ElicitationClient(
        responses=[
            AcceptElicitationResponse(
                action="accept",
                content={"choice": "choice_1"},
            ),
        ],
    )
    session = _session(client)

    result = asyncio.run(
        session.ask_choice(
            "Choose a deployment target",
            [
                ElicitationChoice(value=10, label="Preview"),
                ElicitationChoice(
                    value=20,
                    label="Production",
                    description="Deploy to the production environment.",
                    default=True,
                ),
            ],
        ),
    )

    assert result == ChoiceElicitationAccepted(value=20)
    assert result.status == "accepted"
    assert len(client.calls) == 1
    message, mode = client.calls[0]
    assert message == "Choose a deployment target"
    assert isinstance(mode, ElicitationFormSessionMode)
    assert mode.session_id == "elicitation-session"
    assert mode.requested_schema.required == ["choice"]
    properties = mode.requested_schema.properties
    assert properties is not None
    choice_schema = properties["choice"]
    assert isinstance(choice_schema, ElicitationStringPropertySchema)
    assert choice_schema.default == "choice_1"
    assert choice_schema.one_of is not None
    assert [(option.const, option.title) for option in choice_schema.one_of] == [
        ("choice_0", "Preview"),
        ("choice_1", "Production"),
    ]
    assert choice_schema.one_of[0].field_meta is None
    assert choice_schema.one_of[1].field_meta == {
        "acpkit.dev/choice-description": "Deploy to the production environment.",
    }


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            DeclineElicitationResponse(action="decline"),
            ChoiceElicitationDeclined(),
        ),
        (
            CancelElicitationResponse(action="cancel"),
            ChoiceElicitationCancelled(),
        ),
    ],
)
def test_ask_choice_preserves_non_accepted_outcomes(
    response: CreateElicitationResponse,
    expected: ChoiceElicitationDeclined | ChoiceElicitationCancelled,
) -> None:
    client = _ElicitationClient(responses=[response])

    result = asyncio.run(
        _session(client).ask_choice(
            "Continue?",
            [ElicitationChoice(value=True, label="Continue")],
        ),
    )

    assert result == expected
    assert result.status in {"declined", "cancelled"}


def test_ask_choice_rejects_unsupported_client_without_permission_fallback() -> None:
    client = _ElicitationClient()

    with pytest.raises(ElicitationUnsupportedError, match="does not support form"):
        asyncio.run(
            _session(client, supports_form=False).ask_choice(
                "Continue?",
                [ElicitationChoice(value=True, label="Continue")],
            ),
        )

    assert client.calls == []
    with pytest.raises(AssertionError, match="unexpected elicitation"):
        asyncio.run(client.create_elicitation("unused", cast("Any", object())))


def test_ask_choice_uses_explicit_async_fallback_for_unsupported_client() -> None:
    client = _ElicitationClient()

    async def fallback() -> int:
        await asyncio.sleep(0)
        return 20

    result = asyncio.run(
        _session(client, supports_form=False).ask_choice(
            "Choose a deployment target",
            [
                ElicitationChoice(value=10, label="Preview"),
                ElicitationChoice(value=20, label="Production", default=True),
            ],
            fallback=fallback,
        ),
    )

    assert result == ChoiceElicitationAccepted(value=20)
    assert client.calls == []


@pytest.mark.parametrize(
    "content",
    [
        None,
        {},
        {"choice": 1},
        {"choice": "unknown"},
        {"choice": "choice_nope"},
        {"choice": "choice_9"},
        {"choice": "choice_01"},
    ],
)
def test_ask_choice_rejects_invalid_accepted_response(
    content: dict[str, Any] | None,
) -> None:
    client = _ElicitationClient(
        responses=[AcceptElicitationResponse(action="accept", content=content)],
    )

    with pytest.raises(InvalidElicitationResponseError, match="valid choice"):
        asyncio.run(
            _session(client).ask_choice(
                "Continue?",
                [ElicitationChoice(value=True, label="Continue")],
            ),
        )


@pytest.mark.parametrize(
    ("question", "choices", "message"),
    [
        (" ", [ElicitationChoice(value=1, label="One")], "question must not be blank"),
        ("Choose", [], "choices must not be empty"),
        (
            "Choose",
            [ElicitationChoice(value=1, label=" ")],
            "choice labels must not be blank",
        ),
        (
            "Choose",
            [
                ElicitationChoice(value=1, label="One", default=True),
                ElicitationChoice(value=2, label="Two", default=True),
            ],
            "at most one choice may be the default",
        ),
    ],
)
def test_ask_choice_validates_question_and_choices(
    question: str,
    choices: list[ElicitationChoice[int]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        asyncio.run(_session(_ElicitationClient()).ask_choice(question, choices))


def test_choice_response_parser_rejects_unknown_response_variant() -> None:
    with pytest.raises(AssertionError):
        _parse_choice_response(
            cast("Any", object()),
            [ElicitationChoice(value=1, label="One")],
        )
