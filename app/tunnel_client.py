"""Windows-side Evervault reverse-tunnel client.

Connects outbound to Render over WSS. It does not expose a local port to the
internet. File reads/writes are delegated to the existing authenticated local
storage agent on 127.0.0.1:8766, which keeps all media paths on G: and staging on D:.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import requests
from websockets.asyncio.client import connect

STATE_DIR = Path(os.environ.get("EVERVAULT_STATE_DIR", r"D:\EvervaultState")).expanduser()
TOKEN_FILE = Path(os.environ.get("EVERVAULT_AGENT_TOKEN_FILE", str(STATE_DIR / "storage_agent.token"))).expanduser()
STAGING_DIR = Path(os.environ.get("EVERVAULT_TUNNEL_STAGING_DIR", str(STATE_DIR / "tunnel-staging"))).expanduser()
LOCAL_AGENT_URL = os.environ.get("EVERVAULT_LOCAL_AGENT_URL", "http://127.0.0.1:8766").strip().rstrip("/")
TUNNEL_URL = os.environ.get("EVERVAULT_TUNNEL_URL", "").strip()
CHUNK_SIZE = 256 * 1024
HEARTBEAT_INTERVAL_SECONDS = 300.0
MAX_FILE_SIZE = 100 * 1024**3
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,239}$")


def valid_name(name: str) -> bool:
    return bool(SAFE_NAME.fullmatch(name) and name not in {".", ".."} and ".." not in name)


def validate_tunnel_url(value: str) -> str:
    """Require TLS so the long-lived agent token is never sent over cleartext WS."""
    candidate = (value or "").strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() != "wss"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "EVERVAULT_TUNNEL_URL must be a valid wss:// URL without credentials, query, or fragment"
        )
    return candidate


def read_token() -> str:
    if not TOKEN_FILE.is_file():
        raise RuntimeError(f"Tunnel token file is missing: {TOKEN_FILE}")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("Tunnel token file is empty or unexpectedly short")
    return token


def agent_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {read_token()}"}


def _agent_get(stored_name: str, range_header: str | None) -> requests.Response:
    headers = agent_headers()
    if range_header:
        headers["Range"] = range_header
    return requests.get(
        f"{LOCAL_AGENT_URL}/internal/files/{quote(stored_name, safe='')}",
        headers=headers,
        stream=True,
        timeout=(5, 90),
    )


def _agent_put(stored_name: str, path: Path, mime: str, size: int, sha256_hex: str) -> tuple[int, dict[str, Any] | None, str | None]:
    headers = agent_headers()
    headers.update({
        "Content-Type": mime or "application/octet-stream",
        "X-Evervault-Size": str(size),
        "X-Evervault-SHA256": sha256_hex,
    })
    try:
        with path.open("rb") as stream:
            response = requests.put(
                f"{LOCAL_AGENT_URL}/internal/files/{quote(stored_name, safe='')}",
                headers=headers,
                data=stream,
                timeout=(5, 600),
            )
        try:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            return response.status_code, payload, response.headers.get("content-type")
        finally:
            response.close()
    except Exception as exc:
        # Report only exception class; never print credentials or the tunnel URL.
        print(f"Local storage write failed ({type(exc).__name__}).")
        return 503, None, None


def _read_next(iterator):
    return next(iterator, None)


async def send_json(ws, payload: dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, separators=(",", ":")))


async def handle_get(ws, request: dict[str, Any]) -> None:
    request_id = str(request.get("id", ""))
    stored_name = str(request.get("stored_name", ""))
    if not request_id or not valid_name(stored_name):
        await send_json(ws, {"type": "response", "id": request_id, "status": 400, "headers": {}})
        return

    response = None
    try:
        response = await asyncio.to_thread(_agent_get, stored_name, str(request.get("range") or "") or None)
        headers = {
            key.lower(): response.headers[key]
            for key in ("content-length", "content-range", "accept-ranges", "etag", "last-modified", "content-type")
            if key in response.headers
        }
        await send_json(ws, {"type": "response", "id": request_id, "status": response.status_code, "headers": headers})
        if response.status_code not in (200, 206):
            return

        iterator = response.iter_content(chunk_size=CHUNK_SIZE)
        while True:
            chunk = await asyncio.to_thread(_read_next, iterator)
            if not chunk:
                break
            await ws.send(chunk)
        await send_json(ws, {"type": "end", "id": request_id})
    except Exception as exc:
        print(f"Tunnel photo read failed ({type(exc).__name__}).")
        try:
            await send_json(ws, {"type": "response", "id": request_id, "status": 503, "headers": {}})
        except Exception:
            pass
    finally:
        if response is not None:
            response.close()


async def handle_put(ws, request: dict[str, Any]) -> None:
    request_id = str(request.get("id", ""))
    stored_name = str(request.get("stored_name", ""))
    try:
        size = int(request.get("size", -1))
    except (TypeError, ValueError):
        size = -1
    digest_expected = str(request.get("sha256", "")).lower()
    mime = str(request.get("mime") or "application/octet-stream")[:200]

    if not request_id or not valid_name(stored_name) or size < 1 or size > MAX_FILE_SIZE or not re.fullmatch(r"[a-f0-9]{64}", digest_expected):
        await send_json(ws, {"type": "response", "id": request_id, "status": 400, "json_body": None})
        return

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="tunnel-", suffix=".uploading", dir=str(STAGING_DIR))
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    total = 0
    good = True
    try:
        with os.fdopen(fd, "wb") as output:
            while True:
                frame = await ws.recv()
                if isinstance(frame, str):
                    try:
                        control = json.loads(frame)
                    except ValueError:
                        control = {}
                    if control.get("type") == "request_end" and control.get("id") == request_id:
                        break
                    raise RuntimeError("Unexpected control frame during upload")
                if not isinstance(frame, bytes):
                    raise RuntimeError("Unexpected WebSocket frame")
                total += len(frame)
                if total > size:
                    good = False
                    # Continue draining until request_end to preserve protocol framing.
                    continue
                digest.update(frame)
                output.write(frame)
            output.flush()
            os.fsync(output.fileno())

        observed_hash = digest.hexdigest()
        if not good or total != size or observed_hash != digest_expected:
            await send_json(ws, {"type": "response", "id": request_id, "status": 422, "json_body": None})
            return

        status, payload, content_type = await asyncio.to_thread(
            _agent_put, stored_name, temp_path, mime, size, digest_expected
        )
        await send_json(ws, {
            "type": "response",
            "id": request_id,
            "status": status,
            "json_body": payload,
            "content_type": content_type,
        })
    except Exception as exc:
        print(f"Tunnel upload failed ({type(exc).__name__}).")
        try:
            await send_json(ws, {"type": "response", "id": request_id, "status": 503, "json_body": None})
        except Exception:
            pass
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


async def handle_connection(ws) -> None:
    print("Evervault tunnel connected to Render.")

    async def heartbeat() -> None:
        while True:
            online = False
            try:
                response = await asyncio.to_thread(
                    requests.get,
                    f"{LOCAL_AGENT_URL}/internal/status",
                    headers=agent_headers(),
                    timeout=(3, 5),
                )
                try:
                    if response.status_code == 200:
                        body = response.json()
                        online = bool(body.get("media_drive_online"))
                finally:
                    response.close()
            except Exception:
                online = False
            await send_json(ws, {"type": "heartbeat", "media_drive_online": online, "timestamp": int(time.time())})
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        async for frame in ws:
            if not isinstance(frame, str):
                raise RuntimeError("Unexpected unsolicited binary frame from Render")
            try:
                request = json.loads(frame)
            except ValueError:
                continue
            if not isinstance(request, dict) or request.get("type") != "request":
                continue  # e.g. server's initial protocol-ready message
            method = request.get("method")
            if method == "GET":
                await handle_get(ws, request)
            elif method == "PUT":
                await handle_put(ws, request)
            else:
                await send_json(ws, {"type": "response", "id": str(request.get("id", "")), "status": 405})
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        print("Evervault tunnel disconnected; reconnect will be attempted.")


async def main() -> None:
    tunnel_url = validate_tunnel_url(TUNNEL_URL)
    token = read_token()
    delay = 2
    while True:
        try:
            async with connect(
                tunnel_url,
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=20,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=40,
                max_size=1024 * 1024,
                max_queue=16,
            ) as ws:
                delay = 2
                await handle_connection(ws)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never log the URL, Authorization header, token, or request body.
            print(f"Evervault tunnel reconnecting ({type(exc).__name__}); retry in {delay}s.")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
