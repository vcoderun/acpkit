from __future__ import annotations as _annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast
from weakref import ReferenceType, ref

from ..models import ModelOverride
from ..session.state import AcpSessionContext

__all__ = (
    "assign_model",
    "clear_selected_model_id",
    "default_model",
    "has_native_plan_tools",
    "remember_default_model",
    "selected_model_id",
    "set_active_session",
    "set_native_plan_tools_installed",
    "set_selected_model_id",
    "try_active_session",
)


@dataclass(slots=True)
class _AgentRuntimeState:
    active_session: AcpSessionContext | None = None
    default_model: ModelOverride | None = None
    native_plan_tools_installed: bool = False
    selected_model_id: str | None = None


_STATE: dict[int, _AgentRuntimeState] = {}


class _AgentRuntimeCarrier(Protocol):
    model: ModelOverride


_REFS: dict[int, ReferenceType[Any]] = {}


def _clear_state(agent_id: int) -> None:
    _STATE.pop(agent_id, None)
    _REFS.pop(agent_id, None)


def _state_for(
    agent: Any,
    *,
    create: bool,
) -> _AgentRuntimeState | None:
    agent_id = id(agent)
    state = _STATE.get(agent_id)
    if state is None and create:
        state = _AgentRuntimeState()
        _STATE[agent_id] = state
        with suppress(TypeError):
            _REFS[agent_id] = ref(agent, lambda _: _clear_state(agent_id))
    return state


def try_active_session(
    agent: Any,
) -> AcpSessionContext | None:
    if hasattr(agent, "_acp_active_session"):
        fallback = getattr(agent, "_acp_active_session", None)
        return fallback if isinstance(fallback, AcpSessionContext) else None
    state = _state_for(agent, create=False)
    if state is not None and state.active_session is not None:
        return state.active_session
    return None


def set_active_session(
    agent: Any,
    session: AcpSessionContext,
) -> None:
    state = _state_for(agent, create=True)
    assert state is not None
    state.active_session = session


def default_model(
    agent: Any,
) -> ModelOverride | None:
    if hasattr(agent, "_acp_default_model"):
        return getattr(agent, "_acp_default_model", None)
    state = _state_for(agent, create=False)
    if state is not None and state.default_model is not None:
        return state.default_model
    return None


def remember_default_model(
    agent: Any,
) -> None:
    state = _state_for(agent, create=True)
    assert state is not None
    if state.default_model is None:
        state.default_model = getattr(agent, "model", None)


def selected_model_id(
    agent: Any,
) -> str | None:
    if hasattr(agent, "_acp_selected_model_id"):
        fallback = getattr(agent, "_acp_selected_model_id", None)
        return fallback if isinstance(fallback, str) else None
    state = _state_for(agent, create=False)
    if state is not None and state.selected_model_id is not None:
        return state.selected_model_id
    return None


def set_selected_model_id(
    agent: Any,
    model_id: str,
) -> None:
    state = _state_for(agent, create=True)
    assert state is not None
    state.selected_model_id = model_id
    if hasattr(agent, "_acp_selected_model_id"):
        object.__setattr__(agent, "_acp_selected_model_id", model_id)


def clear_selected_model_id(
    agent: Any,
) -> None:
    state = _state_for(agent, create=True)
    assert state is not None
    state.selected_model_id = None
    if hasattr(agent, "_acp_selected_model_id"):
        object.__setattr__(agent, "_acp_selected_model_id", None)


def has_native_plan_tools(
    agent: Any,
) -> bool:
    state = _state_for(agent, create=False)
    if state is not None and state.native_plan_tools_installed:
        return True
    return bool(getattr(agent, "_acp_native_plan_tools_installed", False))


def set_native_plan_tools_installed(
    agent: Any,
) -> None:
    state = _state_for(agent, create=True)
    assert state is not None
    state.native_plan_tools_installed = True


def assign_model(
    agent: Any,
    model: ModelOverride,
) -> None:
    mutable_agent = cast(_AgentRuntimeCarrier, agent)
    mutable_agent.model = model
