from __future__ import annotations as _annotations

import argparse
import asyncio
import importlib
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, TypeGuard

from acp import run_agent
from acp.interfaces import Agent

from .command import CommandOptions
from .config import TransportOptions, build_server_paths
from .proxy_agent import connect_acp
from .server import serve_acp, serve_stdio_command

__all__ = ("AcpRemoteCliError", "main")

_UNSTABLE_PROTOCOL_HELP = "Enable unstable ACP routes on the receiving upstream connection."


class AcpRemoteCliError(RuntimeError):
    """Raised for user-facing CLI failures."""


@dataclass(frozen=True, slots=True)
class _TargetRef:
    module_name: str
    attribute_path: str | None


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        args.handler(args)
    except AcpRemoteCliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acpremote",
        description="Expose or mirror ACP agents over WebSocket transport.",
    )
    subcommands = parser.add_subparsers(dest="command_name", required=True)

    serve_parser = subcommands.add_parser(
        "serve",
        help="Expose a native ACP Python target over WebSocket.",
    )
    serve_parser.add_argument("target", help="Python target, for example `my_app:agent`.")
    _add_server_options(serve_parser)
    serve_parser.add_argument(
        "-p",
        "--path",
        dest="import_roots",
        action="append",
        default=[],
        help="Extra import root for loading the target module. Can be repeated.",
    )
    serve_parser.set_defaults(handler=_handle_serve)

    expose_parser = subcommands.add_parser(
        "expose",
        help="Expose an ACP-over-stdio command over WebSocket.",
    )
    _add_server_options(expose_parser)
    expose_parser.add_argument("--cwd", help="Working directory for the child command.")
    expose_parser.add_argument(
        "--env",
        dest="env_overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Environment override for the child command. Can be repeated.",
    )
    expose_parser.add_argument(
        "--stderr-mode",
        choices=("inherit", "discard"),
        default="inherit",
        help="How to handle child process stderr.",
    )
    expose_parser.add_argument(
        "--terminate-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait before killing a closing child process.",
    )
    expose_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run. Use `--` before commands that have their own flags.",
    )
    expose_parser.set_defaults(handler=_handle_expose)

    mirror_parser = subcommands.add_parser(
        "mirror",
        help="Mirror a remote ACP WebSocket endpoint back to local stdio ACP.",
    )
    mirror_parser.add_argument("addr", help="Remote ACP WebSocket address.")
    _add_token_options(mirror_parser)
    mirror_parser.add_argument(
        "--unstable-protocol",
        action="store_true",
        help=_UNSTABLE_PROTOCOL_HELP,
    )
    mirror_parser.set_defaults(handler=_handle_mirror)

    return parser


def _add_server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the WebSocket server.",
    )
    parser.add_argument(
        "--port",
        default=8080,
        type=int,
        help="TCP port for the WebSocket server.",
    )
    parser.add_argument(
        "--mount-path",
        default="/acp",
        help="Mount path for metadata and WebSocket routes.",
    )
    _add_token_options(parser)


def _add_token_options(parser: argparse.ArgumentParser) -> None:
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument("--bearer-token", help="Bearer token value.")
    token_group.add_argument(
        "--token-env",
        help="Environment variable containing the bearer token.",
    )


def _handle_serve(args: argparse.Namespace) -> None:
    agent = _load_native_agent_target(
        str(args.target),
        import_roots=tuple(str(path) for path in args.import_roots),
    )
    bearer_token = _resolve_bearer_token(args)

    async def run() -> None:
        server = await serve_acp(
            agent,
            host=str(args.host),
            port=int(args.port),
            mount_path=str(args.mount_path),
            bearer_token=bearer_token,
        )
        await _serve_forever(server, host=str(args.host), mount_path=str(args.mount_path))

    asyncio.run(run())


def _handle_expose(args: argparse.Namespace) -> None:
    command = _normalize_command(tuple(str(part) for part in args.command))
    bearer_token = _resolve_bearer_token(args)
    env = _parse_env_overrides(tuple(str(value) for value in args.env_overrides))
    command_options = CommandOptions(
        command=command,
        cwd=str(args.cwd) if args.cwd is not None else None,
        env=env,
        stderr_mode=args.stderr_mode,
        terminate_timeout=float(args.terminate_timeout),
    )

    async def run() -> None:
        server = await serve_stdio_command(
            command_options,
            host=str(args.host),
            port=int(args.port),
            mount_path=str(args.mount_path),
            bearer_token=bearer_token,
        )
        await _serve_forever(server, host=str(args.host), mount_path=str(args.mount_path))

    asyncio.run(run())


def _handle_mirror(args: argparse.Namespace) -> None:
    connect_kwargs: dict[str, Any] = {
        "bearer_token": _resolve_bearer_token(args),
    }
    if bool(args.unstable_protocol):
        connect_kwargs["options"] = TransportOptions(use_unstable_protocol=True)
    agent = connect_acp(str(args.addr), **connect_kwargs)
    asyncio.run(run_agent(agent))


async def _serve_forever(server: Any, *, host: str, mount_path: str) -> None:
    _print_server_banner(server, host=host, mount_path=mount_path)
    try:
        await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()


def _print_server_banner(server: Any, *, host: str, mount_path: str) -> None:
    port = _server_port(server)
    paths = build_server_paths(mount_path)
    if port is None:
        print(
            f"Serving ACP WebSocket at ws://{host}{paths.websocket_path}",
            file=sys.stderr,
        )
        return
    print(
        f"Serving ACP WebSocket at ws://{host}:{port}{paths.websocket_path}",
        file=sys.stderr,
    )


def _server_port(server: Any) -> int | None:
    sockets = getattr(server, "sockets", None)
    if not sockets:
        return None
    socket = sockets[0]
    address = socket.getsockname()
    if isinstance(address, tuple) and len(address) >= 2 and isinstance(address[1], int):
        return address[1]
    return None


def _resolve_bearer_token(args: argparse.Namespace) -> str | None:
    bearer_token = getattr(args, "bearer_token", None)
    if bearer_token:
        return str(bearer_token)
    token_env = getattr(args, "token_env", None)
    if token_env is None:
        return None
    value = os.getenv(str(token_env), "").strip()
    if value:
        return value
    raise AcpRemoteCliError(f"`{token_env}` is not set or is empty.")


def _normalize_command(command: tuple[str, ...]) -> tuple[str, ...]:
    normalized = command[1:] if command[:1] == ("--",) else command
    if not normalized:
        raise AcpRemoteCliError("Command must not be empty.")
    return normalized


def _parse_env_overrides(values: tuple[str, ...]) -> dict[str, str] | None:
    if not values:
        return None
    env: dict[str, str] = {}
    for value in values:
        key, separator, env_value = value.partition("=")
        if not key or separator == "":
            raise AcpRemoteCliError("Environment overrides must use `KEY=VALUE`.")
        env[key] = env_value
    return env


def _load_native_agent_target(target: str, *, import_roots: Sequence[str]) -> Agent:
    loaded_target = _load_target(target, import_roots=import_roots)
    if _is_acp_agent(loaded_target):
        return loaded_target
    raise AcpRemoteCliError(
        "Target must resolve to a native `acp.interfaces.Agent`. "
        "Use `acpkit serve` for Pydantic or LangChain targets.",
    )


def _load_target(target: str, *, import_roots: Sequence[str]) -> Any:
    reference = _parse_target_ref(target)
    module = _import_target_module(reference, target=target, import_roots=import_roots)
    if reference.attribute_path is None:
        return _resolve_latest_native_agent(module, target)
    return _resolve_attribute(module, reference.attribute_path, target=target)


def _parse_target_ref(target: str) -> _TargetRef:
    module_name, separator, attribute_path = target.partition(":")
    if not module_name:
        raise AcpRemoteCliError(
            "Target must include a module name, for example `my_app` or `my_app:agent`.",
        )
    if separator and not attribute_path:
        raise AcpRemoteCliError("Target attribute cannot be empty.")
    return _TargetRef(
        module_name=module_name,
        attribute_path=attribute_path or None,
    )


def _import_target_module(
    reference: _TargetRef,
    *,
    target: str,
    import_roots: Sequence[str],
) -> ModuleType:
    _ensure_import_root(Path.cwd())
    for import_root in import_roots:
        _ensure_import_root(Path(import_root))
    importlib.invalidate_caches()
    try:
        return importlib.import_module(reference.module_name)
    except ImportError as exc:
        raise AcpRemoteCliError(
            f"Could not import module `{reference.module_name}` from target `{target}`.",
        ) from exc


def _resolve_latest_native_agent(module: ModuleType, target: str) -> Agent:
    latest_target: Agent | None = None
    for value in vars(module).values():
        if _is_acp_agent(value):
            latest_target = value
    if latest_target is None:
        raise AcpRemoteCliError(
            f"Target `{target}` did not resolve to a native ACP agent and the module defines no "
            "native ACP agent instance.",
        )
    return latest_target


def _resolve_attribute(module: ModuleType, attribute_path: str, *, target: str) -> Any:
    value: Any = module
    for attribute_name in attribute_path.split("."):
        try:
            value = getattr(value, attribute_name)
        except AttributeError as exc:
            raise AcpRemoteCliError(
                f"Target `{target}` is missing attribute `{attribute_name}`.",
            ) from exc
    return value


def _ensure_import_root(path: Path) -> None:
    root_path = path.parent if path.exists() and path.is_file() else path
    resolved_path = str(root_path.resolve())
    if resolved_path not in sys.path:
        sys.path.insert(0, resolved_path)


def _is_acp_agent(value: Any) -> TypeGuard[Agent]:
    required_methods = (
        "initialize",
        "new_session",
        "prompt",
        "cancel",
        "on_connect",
    )
    return all(callable(getattr(value, method_name, None)) for method_name in required_methods)
