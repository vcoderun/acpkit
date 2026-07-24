from __future__ import annotations as _annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from acp.interfaces import Agent as AcpAgent
from acp.interfaces import Client as AcpClient
from pydantic_ai.profiles import ModelProfileSpec
from pydantic_ai.settings import ModelSettings

from .client import AcpModel, AcpPromptRenderer, AcpProvider, HistoryMode
from .command_agent import AcpCommandAgent, AcpCommandOptions, CommandStderrMode

__all__ = ("create_acp_model",)


def create_acp_model(
    *,
    acp_agent: AcpAgent | None = None,
    acp_command: Sequence[str] | None = None,
    model_name: str | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stderr_mode: CommandStderrMode = "inherit",
    terminate_timeout: float = 5.0,
    prompt_renderer: AcpPromptRenderer | None = None,
    history_mode: HistoryMode = "latest_user",
    delegate_client: AcpClient | None = None,
    enable_pydantic_acp_meta: bool | None = None,
    auth_method_id: str | None = None,
    raise_on_empty_turn: bool = False,
    settings: ModelSettings | None = None,
    profile: ModelProfileSpec | None = None,
) -> AcpModel:
    """Create a Pydantic AI model backed by an ACP agent or ACP stdio command.

    Exactly one of ``acp_agent`` or ``acp_command`` must be provided. Passing
    ``model_name=None`` leaves ACP model selection to the remote agent's session
    default and does not send a ``session/set_config_option`` request for ``"model"``.
    ``auth_method_id`` selects an explicit ACP authentication method when
    ``session/new`` reports ``auth_required``. Set ``raise_on_empty_turn=True``
    when a silent ACP turn should fail as ``UnexpectedModelBehavior`` instead
    of producing an empty Pydantic AI response.
    """

    command = _normalize_command(acp_command)
    resolved_cwd = Path.cwd() if cwd is None else Path(cwd)
    if command is None:
        if acp_agent is None:
            raise ValueError("Exactly one of acp_agent or acp_command must be provided.")
        source_agent = acp_agent
    else:
        if acp_agent is not None:
            raise ValueError("Exactly one of acp_agent or acp_command must be provided.")
        source_agent = AcpCommandAgent(
            options=AcpCommandOptions(
                command=command,
                cwd=resolved_cwd,
                env=env,
                stderr_mode=stderr_mode,
                terminate_timeout=terminate_timeout,
            ),
        )

    provider = AcpProvider(
        acp_agent=source_agent,
        host_client=delegate_client,
        cwd=resolved_cwd,
        prompt_renderer=prompt_renderer,
        history_mode=history_mode,
        enable_pydantic_acp_meta=enable_pydantic_acp_meta,
        auth_method_id=auth_method_id,
        raise_on_empty_turn=raise_on_empty_turn,
    )
    return provider.model(
        model_name,
        settings=settings,
        profile=profile,
        history_mode=history_mode,
    )


def _normalize_command(command: Sequence[str] | None) -> tuple[str, ...] | None:
    if command is None:
        return None
    if isinstance(command, (str, bytes)):
        raise TypeError("acp_command must be a sequence of command arguments, not a string.")
    normalized = tuple(command)
    if not normalized:
        raise ValueError("acp_command must contain at least one executable argument.")
    return normalized
