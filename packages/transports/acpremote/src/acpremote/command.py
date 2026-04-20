from __future__ import annotations as _annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from .config import TransportOptions

__all__ = ("CommandOptions", "run_remote_command_connection")


@dataclass(frozen=True, kw_only=True)
class CommandOptions:
    command: tuple[str, ...]
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    stderr_mode: Literal["inherit", "discard"] = "inherit"

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command must not be empty")


async def run_remote_command_connection(
    websocket: ServerConnection,
    *,
    command_options: CommandOptions,
    transport_options: TransportOptions | None = None,
) -> None:
    resolved_transport = transport_options or TransportOptions()
    process = await _create_command_process(
        command_options=command_options,
        reader_limit=resolved_transport.reader_limit,
    )

    assert process.stdin is not None
    assert process.stdout is not None

    websocket_to_stdin = asyncio.create_task(
        _relay_websocket_to_stdin(websocket, process.stdin),
        name="acpremote-websocket-to-stdin",
    )
    stdout_to_websocket = asyncio.create_task(
        _relay_stdout_to_websocket(process.stdout, websocket),
        name="acpremote-stdout-to-websocket",
    )
    process_wait = asyncio.create_task(
        process.wait(),
        name="acpremote-command-wait",
    )

    tasks = {websocket_to_stdin, stdout_to_websocket, process_wait}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            _raise_if_needed(task)

        if process_wait in done or stdout_to_websocket in done:
            await _close_websocket(websocket)
        if websocket_to_stdin in done and process.returncode is None:
            process.terminate()

        await _close_stdin(process.stdin)
        if pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)
            for task in done:
                _raise_if_needed(task)
    finally:
        for task in tasks:
            if task.done():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await _close_stdin(process.stdin)
        if process.returncode is None:
            process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
        await _close_websocket(websocket)


async def _create_command_process(
    *,
    command_options: CommandOptions,
    reader_limit: int,
) -> asyncio.subprocess.Process:
    stderr = None if command_options.stderr_mode == "inherit" else asyncio.subprocess.DEVNULL
    return await asyncio.create_subprocess_exec(
        *command_options.command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        cwd=command_options.cwd,
        env=_build_process_env(command_options.env),
        limit=reader_limit,
    )


def _build_process_env(overrides: Mapping[str, str] | None) -> dict[str, str]:
    if overrides is None:
        return dict(os.environ)
    merged = dict(os.environ)
    merged.update(overrides)
    return merged


async def _relay_websocket_to_stdin(
    websocket: ServerConnection,
    stdin: asyncio.StreamWriter,
) -> None:
    try:
        while True:
            message = await websocket.recv()
            if isinstance(message, bytes):
                raise TypeError("binary WebSocket frames are not supported")
            stdin.write(message.encode("utf-8") + b"\n")
            await stdin.drain()
    except ConnectionClosed:
        return
    finally:
        await _close_stdin(stdin)


async def _relay_stdout_to_websocket(
    stdout: asyncio.StreamReader,
    websocket: ServerConnection,
) -> None:
    while True:
        line = await stdout.readline()
        if not line:
            return
        payload = line[:-1] if line.endswith(b"\n") else line
        await websocket.send(payload.decode("utf-8"))


async def _close_stdin(stdin: asyncio.StreamWriter) -> None:
    if stdin.is_closing():
        return
    stdin.close()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        await stdin.wait_closed()


async def _close_websocket(websocket: ServerConnection) -> None:
    with contextlib.suppress(ConnectionClosed):
        await websocket.close()


def _raise_if_needed(task: asyncio.Task[object]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        raise
    except ConnectionClosed:
        return
    except BrokenPipeError:
        return
    except ConnectionResetError:
        return
    except ProcessLookupError:
        return
    except Exception as exc:  # pragma: no cover - defensive logging path
        print(f"acpremote command relay failed: {exc}", file=sys.stderr)
        raise
