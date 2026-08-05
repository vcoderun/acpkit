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
    InvalidElicitationFallbackError,
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


@dataclass(frozen=True, slots=True)
class _SelectiveEqualityValue:
    key: str

    def __eq__(self, other: object) -> bool:
        other_value = cast("_SelectiveEqualityValue", other)
        if other_value.key == "raises":
            raise RuntimeError("comparison is unavailable")
        return self.key == other_value.key


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

    async def fallback() -> ChoiceElicitationAccepted[int]:
        await asyncio.sleep(0)
        return ChoiceElicitationAccepted(value=20)

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


def test_ask_choice_supports_sync_fallback_result() -> None:
    result = asyncio.run(
        _session(_ElicitationClient(), supports_form=False).ask_choice(
            "Continue?",
            [ElicitationChoice(value=True, label="Continue")],
            fallback=lambda: ChoiceElicitationAccepted(value=True),
        ),
    )

    assert result == ChoiceElicitationAccepted(value=True)


def test_ask_choice_accepted_fallback_requires_an_offered_value() -> None:
    session = _session(_ElicitationClient(), supports_form=False)

    with pytest.raises(InvalidElicitationFallbackError, match="was not offered"):
        asyncio.run(
            session.ask_choice(
                "Continue?",
                [ElicitationChoice(value="continue", label="Continue")],
                fallback=lambda: ChoiceElicitationAccepted(value="stop"),
            ),
        )


def test_ask_choice_accepted_fallback_returns_the_canonical_offered_value() -> None:
    selected = _SelectiveEqualityValue("target")

    result = asyncio.run(
        _session(_ElicitationClient(), supports_form=False).ask_choice(
            "Choose a value",
            [ElicitationChoice(value=selected, label="Target")],
            fallback=lambda: ChoiceElicitationAccepted(
                value=_SelectiveEqualityValue("target"),
            ),
        ),
    )

    assert isinstance(result, ChoiceElicitationAccepted)
    assert result.value is selected


@pytest.mark.parametrize(
    "fallback_result",
    [ChoiceElicitationDeclined(), ChoiceElicitationCancelled()],
)
def test_ask_choice_preserves_non_accepted_fallback_results(
    fallback_result: ChoiceElicitationDeclined | ChoiceElicitationCancelled,
) -> None:
    result = asyncio.run(
        _session(_ElicitationClient(), supports_form=False).ask_choice(
            "Continue?",
            [ElicitationChoice(value=True, label="Continue")],
            fallback=lambda: fallback_result,
        ),
    )

    assert result is fallback_result


def test_ask_choice_propagates_fallback_exceptions() -> None:
    def fallback() -> ChoiceElicitationAccepted[bool]:
        raise RuntimeError("fallback failed")

    with pytest.raises(RuntimeError, match="fallback failed"):
        asyncio.run(
            _session(_ElicitationClient(), supports_form=False).ask_choice(
                "Continue?",
                [ElicitationChoice(value=True, label="Continue")],
                fallback=fallback,
            ),
        )


def test_ask_choice_legacy_plain_fallback_requires_an_offered_value() -> None:
    session = _session(_ElicitationClient(), supports_form=False)

    with pytest.warns(DeprecationWarning, match="plain value"):
        result = asyncio.run(
            session.ask_choice(
                "Continue?",
                [ElicitationChoice(value="continue", label="Continue")],
                fallback=lambda: "continue",
            ),
        )
    assert result == ChoiceElicitationAccepted(value="continue")

    with (
        pytest.warns(DeprecationWarning, match="plain value"),
        pytest.raises(InvalidElicitationFallbackError, match="was not offered"),
    ):
        asyncio.run(
            session.ask_choice(
                "Continue?",
                [ElicitationChoice(value="continue", label="Continue")],
                fallback=lambda: "stop",
            ),
        )


def test_ask_choice_legacy_plain_fallback_preserves_none_choice() -> None:
    with pytest.warns(DeprecationWarning, match="plain value"):
        result = asyncio.run(
            _session(_ElicitationClient(), supports_form=False).ask_choice(
                "Choose a value",
                [ElicitationChoice(value=None, label="No value")],
                fallback=lambda: None,
            ),
        )

    assert result == ChoiceElicitationAccepted(value=None)


def test_ask_choice_legacy_fallback_skips_unsafe_equality_and_finds_later_choice() -> None:
    selected = _SelectiveEqualityValue("target")

    with pytest.warns(DeprecationWarning, match="plain value"):
        result = asyncio.run(
            _session(_ElicitationClient(), supports_form=False).ask_choice(
                "Choose a value",
                [
                    ElicitationChoice(
                        value=_SelectiveEqualityValue("raises"),
                        label="Unsafe comparison",
                    ),
                    ElicitationChoice(value=selected, label="Target"),
                ],
                fallback=lambda: _SelectiveEqualityValue("target"),
            ),
        )

    assert result == ChoiceElicitationAccepted(value=selected)


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
