from __future__ import annotations as _annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar, assert_never

from acp.schema import (
    AcceptElicitationResponse,
    CancelElicitationResponse,
    CreateElicitationResponse,
    DeclineElicitationResponse,
    ElicitationFormSessionMode,
    ElicitationMode,
    ElicitationSchema,
    ElicitationStringPropertySchema,
    EnumOption,
)

from .awaitables import resolve_value

__all__ = (
    "ChoiceElicitationAccepted",
    "ChoiceElicitationCancelled",
    "ChoiceElicitationDeclined",
    "ChoiceElicitationResult",
    "ElicitationChoice",
    "ElicitationUnsupportedError",
    "InvalidElicitationResponseError",
)

ChoiceValueT = TypeVar("ChoiceValueT")

_CHOICE_FIELD = "choice"
_CHOICE_DESCRIPTION_META_KEY = "acpkit.dev/choice-description"


@dataclass(frozen=True, slots=True, kw_only=True)
class ElicitationChoice(Generic[ChoiceValueT]):
    """One typed value offered by :meth:`AcpSessionContext.ask_choice`."""

    value: ChoiceValueT
    label: str
    description: str | None = None
    default: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ChoiceElicitationAccepted(Generic[ChoiceValueT]):
    """An accepted elicitation containing the selected typed value."""

    value: ChoiceValueT
    status: Literal["accepted"] = field(init=False, default="accepted")


@dataclass(frozen=True, slots=True, kw_only=True)
class ChoiceElicitationDeclined:
    """An elicitation the user explicitly declined to answer."""

    status: Literal["declined"] = field(init=False, default="declined")


@dataclass(frozen=True, slots=True, kw_only=True)
class ChoiceElicitationCancelled:
    """An elicitation cancelled without an answer."""

    status: Literal["cancelled"] = field(init=False, default="cancelled")


ChoiceElicitationResult: TypeAlias = (
    ChoiceElicitationAccepted[ChoiceValueT] | ChoiceElicitationDeclined | ChoiceElicitationCancelled
)


class ElicitationUnsupportedError(RuntimeError):
    """Raised when the connected ACP client cannot render the requested form."""


class InvalidElicitationResponseError(RuntimeError):
    """Raised when a client accepts an elicitation with an invalid selection."""


class _ElicitationSession(Protocol):
    session_id: str

    def supports_elicitation(self, mode: ElicitationMode) -> bool: ...

    async def create_elicitation(
        self,
        message: str,
        mode: ElicitationMode,
    ) -> CreateElicitationResponse: ...


async def _ask_choice(
    session: _ElicitationSession,
    question: str,
    choices: Sequence[ElicitationChoice[ChoiceValueT]],
    *,
    fallback: Callable[[], ChoiceValueT | Awaitable[ChoiceValueT]] | None,
) -> ChoiceElicitationResult[ChoiceValueT]:
    normalized_choices = _validate_choices(question, choices)
    mode = _choice_form_mode(session.session_id, normalized_choices)
    if not session.supports_elicitation(mode):
        if fallback is None:
            raise ElicitationUnsupportedError(
                "The connected ACP client does not support form elicitation.",
            )
        fallback_value = await resolve_value(fallback())
        return ChoiceElicitationAccepted(value=fallback_value)

    response = await session.create_elicitation(question, mode)
    return _parse_choice_response(response, normalized_choices)


def _validate_choices(
    question: str,
    choices: Sequence[ElicitationChoice[ChoiceValueT]],
) -> tuple[ElicitationChoice[ChoiceValueT], ...]:
    if not question.strip():
        raise ValueError("question must not be blank")
    normalized_choices = tuple(choices)
    if not normalized_choices:
        raise ValueError("choices must not be empty")
    if any(not choice.label.strip() for choice in normalized_choices):
        raise ValueError("choice labels must not be blank")
    if sum(choice.default for choice in normalized_choices) > 1:
        raise ValueError("at most one choice may be the default")
    return normalized_choices


def _choice_form_mode(
    session_id: str,
    choices: Sequence[ElicitationChoice[ChoiceValueT]],
) -> ElicitationFormSessionMode:
    options: list[EnumOption] = []
    default_token: str | None = None
    for index, choice in enumerate(choices):
        token = _choice_token(index)
        field_meta = (
            {_CHOICE_DESCRIPTION_META_KEY: choice.description}
            if choice.description is not None
            else None
        )
        options.append(EnumOption(const=token, title=choice.label, field_meta=field_meta))
        if choice.default:
            default_token = token

    return ElicitationFormSessionMode(
        session_id=session_id,
        requested_schema=ElicitationSchema(
            properties={
                _CHOICE_FIELD: ElicitationStringPropertySchema(
                    type="string",
                    title="Choice",
                    one_of=options,
                    default=default_token,
                ),
            },
            required=[_CHOICE_FIELD],
        ),
    )


def _parse_choice_response(
    response: CreateElicitationResponse,
    choices: Sequence[ElicitationChoice[ChoiceValueT]],
) -> ChoiceElicitationResult[ChoiceValueT]:
    if isinstance(response, AcceptElicitationResponse):
        content = response.content
        token = content.get(_CHOICE_FIELD) if isinstance(content, dict) else None
        if isinstance(token, str) and token.startswith("choice_"):
            index_text = token.removeprefix("choice_")
            if index_text.isdigit():
                index = int(index_text)
                if index < len(choices) and token == _choice_token(index):
                    return ChoiceElicitationAccepted(value=choices[index].value)
        raise InvalidElicitationResponseError(
            "The ACP client accepted the elicitation without a valid choice.",
        )
    if isinstance(response, DeclineElicitationResponse):
        return ChoiceElicitationDeclined()
    if isinstance(response, CancelElicitationResponse):
        return ChoiceElicitationCancelled()
    assert_never(response)


def _choice_token(index: int) -> str:
    return f"choice_{index}"
