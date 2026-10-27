from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.tunnel import TunnelManager, bearer_matches, valid_stored_name
from app.tunnel_client import validate_tunnel_url


class FakeWebSocket:
    """Small in-memory stand-in for Starlette WebSocket used by protocol tests."""
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.closed = False

    async def send_json(self, data):
        await self.outgoing.put({"kind": "text", "value": data})

    async def send_bytes(self, data):
        await self.outgoing.put({"kind": "bytes", "value": data})

    async def receive(self):
        return await self.incoming.get()

    async def close(self, code=1000, reason=""):
        self.closed = True
        await self.incoming.put({"type": "websocket.disconnect", "code": code})

    async def client_send_json(self, data):
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def client_send_bytes(self, data):
        await self.incoming.put({"type": "websocket.receive", "bytes": data})


class TunnelProtocolTests(unittest.TestCase):
    def test_bearer_auth_uses_expected_secret(self):
        self.assertTrue(bearer_matches("Bearer test-secret", "test-secret"))
        self.assertFalse(bearer_matches("Bearer wrong", "test-secret"))
        self.assertFalse(bearer_matches(None, "test-secret"))
        self.assertFalse(bearer_matches("Bearer test-secret", ""))

    def test_stored_name_validation_rejects_paths_and_traversal(self):
        self.assertTrue(valid_stored_name("a83d1f-photo.jpg"))
        self.assertFalse(valid_stored_name("../secret.txt"))
        self.assertFalse(valid_stored_name("subdir/photo.jpg"))
        self.assertFalse(valid_stored_name("bad..name.jpg"))
        self.assertFalse(valid_stored_name(""))

    def test_tunnel_url_requires_encrypted_wss_without_url_credentials(self):
        good = "wss://evervault-94v1.onrender.com/internal/tunnel/ws"
        self.assertEqual(validate_tunnel_url(good), good)
        for bad in (
            "ws://evervault-94v1.onrender.com/internal/tunnel/ws",
            "http://evervault-94v1.onrender.com/internal/tunnel/ws",
            "",
            "wss://user:pass@example.com/tunnel",
            "wss://example.com/tunnel?token=abc",
            "wss://example.com/tunnel#fragment",
        ):
            with self.subTest(url=bad):
                with self.assertRaises(RuntimeError):
                    validate_tunnel_url(bad)


class TunnelManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = TunnelManager()
        self.ws = FakeWebSocket()
        self.connection_task = asyncio.create_task(self.manager.serve_connection(self.ws))
        ready = await asyncio.wait_for(self.ws.outgoing.get(), timeout=1)
        self.assertEqual(ready["value"]["type"], "ready")
        await self.ws.client_send_json({"type": "heartbeat", "media_drive_online": True})
        await asyncio.sleep(0.01)
        self.assertTrue(self.manager.connected)

    async def asyncTearDown(self):
        if not self.ws.closed:
            await self.ws.close()
        try:
            await asyncio.wait_for(self.connection_task, timeout=1)
        except asyncio.TimeoutError:
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass

    async def test_download_stream_protocol_preserves_binary_chunks(self):
        open_task = asyncio.create_task(self.manager.open_download("photo.jpg", "bytes=0-9"))
        outbound = await asyncio.wait_for(self.ws.outgoing.get(), timeout=1)
        request = outbound["value"]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["stored_name"], "photo.jpg")
        self.assertEqual(request["range"], "bytes=0-9")

        await self.ws.client_send_json({
            "type": "response", "id": request["id"], "status": 206,
            "headers": {"content-type": "image/jpeg", "content-range": "bytes 0-9/10", "content-length": "10"},
        })
        await self.ws.client_send_bytes(b"12345")
        await self.ws.client_send_bytes(b"67890")
        await self.ws.client_send_json({"type": "end", "id": request["id"]})

        status, headers, body = await asyncio.wait_for(open_task, timeout=1)
        self.assertEqual(status, 206)
        self.assertEqual(headers["content-range"], "bytes 0-9/10")
        received = b"".join([chunk async for chunk in body])
        self.assertEqual(received, b"1234567890")
        self.assertFalse(self.manager._operation_guard.locked())

    async def test_cancelled_photo_request_before_response_resets_tunnel_and_releases_lock(self):
        request_task = asyncio.create_task(self.manager.open_download("photo.jpg"))
        await asyncio.wait_for(self.ws.outgoing.get(), timeout=1)
        request_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request_task
        await asyncio.sleep(0.02)
        self.assertTrue(self.ws.closed)
        self.assertFalse(self.manager.connected)
        self.assertFalse(self.manager._operation_guard.locked())

    async def test_cancelled_download_resets_tunnel_and_releases_lock(self):
        open_task = asyncio.create_task(self.manager.open_download("photo.jpg"))
        outbound = await asyncio.wait_for(self.ws.outgoing.get(), timeout=1)
        request = outbound["value"]
        await self.ws.client_send_json({
            "type": "response", "id": request["id"], "status": 200,
            "headers": {"content-type": "image/jpeg"},
        })
        status, _, body = await asyncio.wait_for(open_task, timeout=1)
        self.assertEqual(status, 200)

        async def consume_one_chunk():
            async for _chunk in body:
                return

        consumer = asyncio.create_task(consume_one_chunk())
        await self.ws.client_send_bytes(b"first chunk")
        await asyncio.wait_for(consumer, timeout=1)
        # Explicitly close the body early, simulating a browser abandoning the stream.
        await body.aclose()
        await asyncio.sleep(0.02)
        self.assertTrue(self.ws.closed)
        self.assertFalse(self.manager.connected)
        self.assertFalse(self.manager._operation_guard.locked())

    async def test_upload_stream_protocol_sends_verified_source_chunks(self):
        temp_root = Path(os.environ.get("EVERVAULT_TEST_TEMP_ROOT", r"D:\EvervaultState\tmp" if os.name == "nt" else tempfile.gettempdir()))
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="ev-tunnel-protocol-", suffix=".bin", dir=str(temp_root), delete=False) as file:
            file.write(b"tunnel upload test body")
            source = Path(file.name)
        try:
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            upload_task = asyncio.create_task(self.manager.upload_file(source, "upload-test.bin", "application/octet-stream", len(data), digest))
            request_frame = await asyncio.wait_for(self.ws.outgoing.get(), timeout=1)
            request = request_frame["value"]
            self.assertEqual(request["type"], "request")
            self.assertEqual(request["method"], "PUT")
            self.assertEqual(request["sha256"], digest)

            sent = bytearray()
            while True:
                frame = await asyncio.wait_for(self.ws.outgoing.get(), timeout=1)
                if frame["kind"] == "bytes":
                    sent.extend(frame["value"])
                elif frame["value"].get("type") == "request_end":
                    self.assertEqual(frame["value"]["id"], request["id"])
                    break
                else:
                    self.fail(f"Unexpected frame: {frame!r}")
            self.assertEqual(bytes(sent), data)

            await self.ws.client_send_json({
                "type": "response", "id": request["id"], "status": 200,
                "json_body": {"ok": True, "stored_name": "upload-test.bin", "size": len(data), "sha256": digest},
            })
            result = await asyncio.wait_for(upload_task, timeout=1)
            self.assertEqual(result["sha256"], digest)
            self.assertFalse(self.manager._operation_guard.locked())
        finally:
            source.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
