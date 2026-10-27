"""Authenticated, single-PC reverse WebSocket tunnel for Evervault.

The Windows client initiates the WebSocket connection. Render never opens an
inbound connection to the user's PC. The protocol intentionally serializes one
file operation at a time to keep streaming frames unambiguous and bounded.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import WebSocket, WebSocketDisconnect

CHUNK_SIZE = 256 * 1024
MESSAGE_TIMEOUT_SECONDS = 180.0
HEARTBEAT_INTERVAL_SECONDS = 300.0
CONNECTION_STALE_SECONDS = 660.0
_DISCONNECTED = object()


class TunnelUnavailable(RuntimeError):
    """Raised when the Windows storage client is not connected or has failed."""


def bearer_matches(authorization: str | None, expected_token: str | None) -> bool:
    """Constant-time validation of the Bearer header used for tunnel auth."""
    expected = (expected_token or "").strip()
    supplied = (authorization or "")
    if supplied.startswith("Bearer "):
        supplied = supplied[7:].strip()
    else:
        supplied = ""
    return bool(expected) and hmac.compare_digest(supplied, expected)


def valid_stored_name(name: str) -> bool:
    """Accept the generated names used by Evervault; reject paths/traversal."""
    import re

    return bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}", name)
        and name not in {".", ".."}
        and ".." not in name
    )


@dataclass
class _TunnelConnection:
    websocket: WebSocket
    # Bound queued binary chunks to provide backpressure during large/slow streams.
    incoming: asyncio.Queue[Any] = field(default_factory=lambda: asyncio.Queue(maxsize=8))
    closed: bool = False
    connected_at: float = field(default_factory=time.monotonic)
    last_heartbeat: float = field(default_factory=time.monotonic)
    media_drive_online: bool = False


class TunnelManager:
    """One connected Windows client and a single serialized streaming operation."""

    def __init__(self) -> None:
        self._connection: _TunnelConnection | None = None
        self._connection_guard = asyncio.Lock()
        self._operation_guard = asyncio.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def connected(self) -> bool:
        conn = self._connection
        return bool(conn and not conn.closed and (time.monotonic() - conn.last_heartbeat) < CONNECTION_STALE_SECONDS)

    def status(self) -> dict[str, Any]:
        conn = self._connection
        connected = self.connected
        return {
            "connected": connected,
            "media_drive_online": bool(conn and connected and conn.media_drive_online),
            "last_heartbeat_seconds_ago": round(time.monotonic() - conn.last_heartbeat, 1) if conn else None,
        }

    @staticmethod
    def _signal_disconnected(conn: _TunnelConnection) -> None:
        # Free bounded-queue slots so a blocked receiver can wake up and observe close.
        while True:
            try:
                conn.incoming.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            conn.incoming.put_nowait(_DISCONNECTED)
        except asyncio.QueueFull:
            pass

    async def serve_connection(self, websocket: WebSocket) -> None:
        """Own the WebSocket receive loop and dispatch frames to active operations."""
        self.loop = asyncio.get_running_loop()
        conn = _TunnelConnection(websocket=websocket)
        async with self._connection_guard:
            old = self._connection
            self._connection = conn
        if old and not old.closed:
            old.closed = True
            try:
                await old.websocket.close(code=1012, reason="Replaced by a newer Evervault client")
            except Exception:
                pass
            self._signal_disconnected(old)

        try:
            await websocket.send_json({"type": "ready", "protocol": 1})
            while True:
                packet = await websocket.receive()
                if packet.get("type") == "websocket.disconnect":
                    break
                text = packet.get("text")
                binary = packet.get("bytes")
                if text is not None:
                    try:
                        value = json.loads(text)
                    except (TypeError, ValueError):
                        await conn.incoming.put({"kind": "invalid_text"})
                        continue
                    if isinstance(value, dict) and value.get("type") == "heartbeat":
                        conn.last_heartbeat = time.monotonic()
                        conn.media_drive_online = bool(value.get("media_drive_online", False))
                        continue
                    await conn.incoming.put({"kind": "text", "value": value})
                elif binary is not None:
                    await conn.incoming.put({"kind": "bytes", "value": binary})
        except WebSocketDisconnect:
            pass
        except Exception:
            # Don't log headers, payloads, tokens, or file names here.
            pass
        finally:
            conn.closed = True
            self._signal_disconnected(conn)
            async with self._connection_guard:
                if self._connection is conn:
                    self._connection = None

    async def _active_connection(self) -> _TunnelConnection:
        conn = self._connection
        if not conn or conn.closed or (time.monotonic() - conn.last_heartbeat) >= CONNECTION_STALE_SECONDS:
            raise TunnelUnavailable("The Evervault Windows client is not connected")
        return conn

    async def _receive(self, conn: _TunnelConnection) -> dict[str, Any]:
        try:
            packet = await asyncio.wait_for(conn.incoming.get(), timeout=MESSAGE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TunnelUnavailable("Timed out waiting for the Windows client") from exc
        if packet is _DISCONNECTED:
            raise TunnelUnavailable("The Evervault tunnel disconnected")
        return packet

    async def _receive_control(self, conn: _TunnelConnection, request_id: str) -> dict[str, Any]:
        while True:
            packet = await self._receive(conn)
            if packet.get("kind") != "text":
                raise TunnelUnavailable("Unexpected binary frame in tunnel control response")
            value = packet.get("value")
            if not isinstance(value, dict) or value.get("id") != request_id or value.get("type") != "response":
                raise TunnelUnavailable("Invalid tunnel response")
            return value

    async def _reset_interrupted_connection(self, conn: _TunnelConnection, reason: str) -> None:
        """Fail closed after an interrupted stream so unread frames cannot poison the next request."""
        conn.closed = True
        self._signal_disconnected(conn)
        try:
            close_task = asyncio.create_task(conn.websocket.close(code=1012, reason=reason[:120]))
            await asyncio.shield(close_task)
        except BaseException:
            # The socket may already be closing, or this task may itself be cancelled.
            pass

    async def open_download(
        self,
        stored_name: str,
        range_header: str | None = None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes] | None]:
        """Start a remote GET and return a streaming body that owns the operation lock."""
        if not valid_stored_name(stored_name):
            raise ValueError("Invalid stored filename")
        await self._operation_guard.acquire()
        lock_owned = True
        conn: _TunnelConnection | None = None
        request_sent = False
        try:
            conn = await self._active_connection()
            request_id = uuid.uuid4().hex
            await conn.websocket.send_json({
                "type": "request",
                "id": request_id,
                "method": "GET",
                "stored_name": stored_name,
                "range": range_header or "",
            })
            request_sent = True
            response = await self._receive_control(conn, request_id)
            status_code = int(response.get("status", 502))
            headers = response.get("headers", {})
            if not isinstance(headers, dict):
                headers = {}
            headers = {str(k).lower(): str(v) for k, v in headers.items() if str(k).lower() in {
                "content-length", "content-range", "accept-ranges", "etag", "last-modified", "content-type"
            }}
            if status_code not in (200, 206):
                self._operation_guard.release()
                lock_owned = False
                return status_code, headers, None

            async def body() -> AsyncIterator[bytes]:
                completed = False
                try:
                    while True:
                        packet = await self._receive(conn)
                        if packet.get("kind") == "bytes":
                            chunk = packet.get("value")
                            if chunk:
                                yield chunk
                            continue
                        if packet.get("kind") == "text":
                            value = packet.get("value")
                            if isinstance(value, dict) and value.get("id") == request_id and value.get("type") == "end":
                                completed = True
                                return
                            raise TunnelUnavailable("Unexpected control frame while streaming photo")
                        raise TunnelUnavailable("Invalid frame while streaming photo")
                finally:
                    # This finally path also handles asyncio.CancelledError and GeneratorExit,
                    # which do not derive from Exception in modern Python versions. If the
                    # consumer stops early, reset the socket before another operation may start.
                    try:
                        if not completed:
                            await self._reset_interrupted_connection(conn, "Photo stream interrupted")
                    finally:
                        if self._operation_guard.locked():
                            self._operation_guard.release()

            return status_code, headers, body()
        except asyncio.CancelledError:
            # Cancellation can happen while waiting for the initial GET response,
            # before the StreamingResponse body generator exists. Fail closed and
            # release the operation lock so one disconnected browser cannot wedge
            # every later photo request.
            if request_sent and conn and not conn.closed:
                await self._reset_interrupted_connection(conn, "Photo request cancelled")
            if lock_owned and self._operation_guard.locked():
                self._operation_guard.release()
            raise
        except Exception:
            if request_sent and conn and not conn.closed:
                await self._reset_interrupted_connection(conn, "Photo request failed")
            if lock_owned and self._operation_guard.locked():
                self._operation_guard.release()
            raise

    async def upload_file(
        self,
        source: Path,
        stored_name: str,
        mime: str,
        size: int,
        sha256_hex: str,
    ) -> dict[str, Any] | None:
        """Stream a finished upload file to the connected Windows client."""
        if not valid_stored_name(stored_name) or size < 1 or len(sha256_hex) != 64:
            return None
        await self._operation_guard.acquire()
        conn: _TunnelConnection | None = None
        request_sent = False
        completed = False
        try:
            conn = await self._active_connection()
            request_id = uuid.uuid4().hex
            await conn.websocket.send_json({
                "type": "request",
                "id": request_id,
                "method": "PUT",
                "stored_name": stored_name,
                "mime": mime or "application/octet-stream",
                "size": int(size),
                "sha256": sha256_hex.lower(),
            })
            request_sent = True
            with source.open("rb") as stream:
                while True:
                    chunk = await asyncio.to_thread(stream.read, CHUNK_SIZE)
                    if not chunk:
                        break
                    await conn.websocket.send_bytes(chunk)
            await conn.websocket.send_json({"type": "request_end", "id": request_id})
            response = await self._receive_control(conn, request_id)
            completed = True
            payload = response.get("json_body")
            if response.get("status") != 200 or not isinstance(payload, dict):
                return None
            return payload
        except asyncio.CancelledError:
            if request_sent and conn and not conn.closed:
                await self._reset_interrupted_connection(conn, "Upload cancelled")
            raise
        except Exception:
            # If the protocol was interrupted mid-transfer, close the socket so the PC
            # discards its staged bytes and reconnects before a later operation begins.
            if request_sent and conn and not conn.closed:
                await self._reset_interrupted_connection(conn, "Upload interrupted")
            raise
        finally:
            if self._operation_guard.locked():
                self._operation_guard.release()

    def upload_file_blocking(
        self,
        source: Path,
        stored_name: str,
        mime: str,
        size: int,
        sha256_hex: str,
    ) -> dict[str, Any] | None:
        """Bridge the existing sync upload handler to the WebSocket event loop."""
        loop = self.loop
        if not loop or loop.is_closed() or not self.connected:
            return None
        future = None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.upload_file(source, stored_name, mime, size, sha256_hex), loop
            )
            # Large media over home broadband can take several minutes.
            timeout = max(600, min(21600, int(size / (64 * 1024)) + 600))
            return future.result(timeout=timeout)
        except TimeoutError:
            if future is not None:
                future.cancel()
            conn = self._connection
            if conn and not conn.closed and not loop.is_closed():
                try:
                    asyncio.run_coroutine_threadsafe(
                        conn.websocket.close(code=1012, reason="Upload timed out; resetting tunnel"), loop
                    )
                except Exception:
                    pass
            return None
        except Exception:
            if future is not None and not future.done():
                future.cancel()
            return None



TUNNEL_MANAGER = TunnelManager()
