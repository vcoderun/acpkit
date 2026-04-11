from __future__ import annotations as _annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from acp.exceptions import RequestError
from pydantic_ai import Agent as PydanticAgent

from ..models import AdapterModel, ModelOverride
from ..session.state import AcpSessionContext
from ._agent_state import (
    assign_model,
    clear_selected_model_id,
    default_model,
    remember_default_model,
    selected_model_id,
    set_selected_model_id,
)
from .slash_commands import (
    extract_session_mcp_servers,
    list_agent_tools,
    render_hook_listing,
    render_mcp_server_listing,
    render_mode_message,
    render_model_message,
    render_thinking_message,
    render_tool_listing,
)

if TYPE_CHECKING:
    from ._session_runtime import _SessionRuntime

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("_SessionModelRuntime",)


class _SessionModelRuntime(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, runtime: _SessionRuntime[AgentDepsT, OutputDataT]) -> None:
        self._runtime = runtime

    async def handle_slash_command(
        self,
        command_name: str,
        *,
        argument: str | None,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        mode_state = await self._runtime._get_mode_state(session, agent)
        if mode_state is not None and command_name in {mode.id for mode in mode_state.modes}:
            if await self._runtime.set_session_mode(command_name, session.session_id) is None:
                return f"Mode `{command_name}` is unavailable"
            return render_mode_message(command_name)
        if command_name == "model":
            if argument is None:
                return render_model_message(self._runtime._resolve_current_model_id(session, agent))
            self._runtime._set_agent_model(agent, session, argument)
            surface = await self._runtime._build_session_surface(session, agent)
            await self._runtime._emit_session_state_updates(
                session,
                surface,
                emit_available_commands=True,
                emit_config_options=True,
                emit_current_mode=False,
                emit_plan=True,
                emit_session_info=True,
            )
            return render_model_message(self._runtime._resolve_current_model_id(session, agent))
        if command_name == "thinking":
            if argument is None:
                return render_thinking_message(
                    self._runtime._current_thinking_value(session, agent)
                )
            normalized_argument = argument.strip().lower()
            if (
                await self._runtime.set_config_option(
                    "thinking", session.session_id, normalized_argument
                )
                is None
            ):
                return "Thinking effort is unavailable or invalid"
            return render_thinking_message(normalized_argument)
        if command_name == "tools":
            return render_tool_listing(list_agent_tools(agent))
        if command_name == "hooks":
            return render_hook_listing(
                self._runtime._owner._list_agent_hooks(agent),
                projection_map=self._runtime._owner._config.hook_projection_map,
            )
        if command_name == "mcp-servers":
            return render_mcp_server_listing(
                extract_session_mcp_servers(session, agent=agent),
            )
        return None

    def apply_session_model_to_agent(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> None:
        session_model_id = session.session_model_id
        if session_model_id is None:
            if selected_model_id(agent) is not None:
                self.restore_agent_default_model(agent)
            return
        if selected_model_id(agent) == session_model_id:
            return
        self.set_agent_model(agent, session, session_model_id)

    def set_agent_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
        model_id: str,
    ) -> None:
        self.remember_default_model(agent)
        selected_model = self.resolve_selected_model(model_id)
        assign_model(agent, selected_model)
        normalized_model_id = model_id.strip()
        set_selected_model_id(agent, normalized_model_id)
        session.session_model_id = normalized_model_id
        session.config_values["model"] = normalized_model_id

    def restore_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> bool:
        if session.session_model_id is None:
            return False
        if not self.restore_agent_default_model(agent):
            return False
        restored_model_id = self._runtime._resolve_model_id_from_value(default_model(agent))
        session.session_model_id = restored_model_id
        if restored_model_id is None:
            session.config_values.pop("model", None)
        else:
            session.config_values["model"] = restored_model_id
        return True

    def restore_agent_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> bool:
        agent_default_model = default_model(agent)
        if agent_default_model is None:
            return False
        assign_model(agent, agent_default_model)
        clear_selected_model_id(agent)
        return True

    def remember_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        remember_default_model(agent)

    def resolve_selected_model(self, model_id: str) -> ModelOverride:
        normalized_model_id = model_id.strip()
        if not normalized_model_id:
            raise RequestError.invalid_params({"modelId": model_id})
        model_option = self._runtime._find_model_option(normalized_model_id)
        if model_option is not None:
            return self.resolve_model_option(model_option)
        return self.resolve_unconfigured_model_id(normalized_model_id)

    def resolve_model_option(self, model_option: AdapterModel) -> ModelOverride:
        if (
            isinstance(model_option.override, str)
            and model_option.override.strip() == model_option.model_id.strip()
        ):
            return self.resolve_unconfigured_model_id(model_option.model_id)
        return model_option.override

    def resolve_unconfigured_model_id(self, model_id: str) -> ModelOverride:
        normalized_model_id = model_id.strip()
        if normalized_model_id.startswith("codex:"):
            codex_model_id = normalized_model_id.removeprefix("codex:").strip()
            if not codex_model_id:
                raise RequestError.invalid_params({"modelId": model_id})
            try:
                from codex_auth_helper import create_codex_responses_model
            except ImportError as exc:
                raise RequestError.invalid_params({"modelId": normalized_model_id}) from exc
            return create_codex_responses_model(codex_model_id)
        return normalized_model_id
