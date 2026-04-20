from __future__ import annotations as _annotations

import asyncio
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from websockets.asyncio.client import ClientConnection
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

__all__ = ("WebSocketStreamBridge", "open_websocket_stream_bridge")


WebSocketConnection: TypeAlias = ServerConnection | ClientConnection


class _TextWebSocket(Protocol):
    async def recv(self, decode: bool | None = None) -> str | bytes: ...

    async def send(self, message: str) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...

    async def wait_closed(self) -> None: ...


@dataclass(slots=True)
class _PendingWrite:
    payload: str
    future: asyncio.Future[None]


class _WriterProtocol(asyncio.Protocol):
    def __init__(self, *, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._close_waiter: asyncio.Future[None] = loop.create_future()
        self._transport: _WebSocketTransport | None = None

    def bind_transport(self, transport: _WebSocketTransport) -> None:
        self._transport = transport

    async def _drain_helper(self) -> None:
        if self._transport is not None:
            await self._transport.wait_flushed()

    def _get_close_waiter(self, stream: asyncio.StreamWriter) -> Awaitable[None]:
        del stream
        return self._close_waiter

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is None:
            if not self._close_waiter.done():
                self._close_waiter.set_result(None)
            return
        if not self._close_waiter.done():
            self._close_waiter.set_exception(exc)


class _WebSocketTransport(asyncio.Transport):
    def __init__(
        self,
        *,
        websocket: _TextWebSocket,
        loop: asyncio.AbstractEventLoop,
        protocol: _WriterProtocol,
    ) -> None:
        super().__init__()
        self._websocket = websocket
        self._loop = loop
        self._protocol = protocol
        self._buffer = bytearray()
        self._pending: asyncio.Queue[_PendingWrite | None] = asyncio.Queue()
        self._closed = False
        self._last_flush: asyncio.Future[None] = loop.create_future()
        self._last_flush.set_result(None)
        self._sender_task = loop.create_task(self._sender_loop())

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if self._closed:
            raise ConnectionResetError("transport is closing")
        self._buffer.extend(bytes(data))
        while True:
            newline_index = self._buffer.find(b"\n")
            if newline_index < 0:
                return
            raw_line = bytes(self._buffer[:newline_index])
            del self._buffer[: newline_index + 1]
            payload = raw_line.decode("utf-8")
            flush_future: asyncio.Future[None] = self._loop.create_future()
            self._last_flush = flush_future
            self._pending.put_nowait(_PendingWrite(payload=payload, future=flush_future))

    def writelines(
        self,
        list_of_data: Iterable[bytes | bytearray | memoryview],
    ) -> None:
        for line in list_of_data:
            self.write(line)

    def can_write_eof(self) -> bool:
        return False

    def write_eof(self) -> None:
        self.close()

    def is_closing(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending.put_nowait(None)

    def get_extra_info(self, name: str, default: object = None) -> object:
        del name
        return default

    async def wait_flushed(self) -> None:
        await self._last_flush

    async def aclose(self) -> None:
        self.close()
        try:
            await self._sender_task
        finally:
            await self._websocket.close()
            await self._websocket.wait_closed()

    async def _sender_loop(self) -> None:
        connection_error: Exception | None = None
        try:
            while True:
                item = await self._pending.get()
                if item is None:
                    break
                try:
                    await self._websocket.send(item.payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - exercised via connection loss
                    connection_error = exc
                    if not item.future.done():
                        item.future.set_exception(exc)
                    break
                if not item.future.done():
                    item.future.set_result(None)
        finally:
            while not self._pending.empty():
                item = self._pending.get_nowait()
                if item is not None and not item.future.done():
                    item.future.set_exception(ConnectionResetError("connection closed"))
            self._protocol.connection_lost(connection_error)


@dataclass(slots=True)
class WebSocketStreamBridge:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    _transport: _WebSocketTransport
    _reader_task: asyncio.Task[None]

    async def close(self) -> None:
        self._transport.close()
        await self._transport.aclose()
        await self._reader_task


async def open_websocket_stream_bridge(
    websocket: WebSocketConnection,
    *,
    reader_limit: int | None = None,
) -> WebSocketStreamBridge:
    loop = asyncio.get_running_loop()
    reader = (
        asyncio.StreamReader(limit=reader_limit)
        if reader_limit is not None
        else asyncio.StreamReader()
    )
    protocol = _WriterProtocol(loop=loop)
    transport = _WebSocketTransport(websocket=websocket, loop=loop, protocol=protocol)
    protocol.bind_transport(transport)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    reader_task = loop.create_task(_reader_loop(websocket, reader))
    return WebSocketStreamBridge(
        reader=reader,
        writer=writer,
        _transport=transport,
        _reader_task=reader_task,
    )


async def _reader_loop(
    websocket: _TextWebSocket,
    reader: asyncio.StreamReader,
) -> None:
    try:
        while True:
            message = await websocket.recv()
            if isinstance(message, bytes):
                raise TypeError("binary WebSocket frames are not supported")
            reader.feed_data(message.encode("utf-8") + b"\n")
    except ConnectionClosed:
        reader.feed_eof()
    except asyncio.CancelledError:
        reader.feed_eof()
        raise
    except Exception as exc:
        reader.set_exception(exc)
        reader.feed_eof()
        raise
