from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PatchWiringTests(unittest.TestCase):
    def test_render_websocket_route_is_opt_in_and_authenticated(self):
        source = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn('TUNNEL_ENABLED = os.environ.get("EVERVAULT_TUNNEL_ENABLED", "0").strip() == "1"', source)
        self.assertIn('@app.websocket("/internal/tunnel/ws")', source)
        self.assertIn('openapi_url=None', source)
        self.assertIn('bearer_matches(websocket.headers.get("authorization"), LOCAL_STORAGE_TOKEN)', source)
        self.assertIsNotNone(next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "delete_media"), None))

    def test_remote_photo_route_uses_tunnel_only_when_enabled(self):
        source = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
        self.assertIn('async def media_file(', source)
        self.assertIn('if USE_POSTGRES and TUNNEL_ENABLED:', source)
        self.assertIn('await TUNNEL_MANAGER.open_download(', source)

    def test_windows_client_uses_outbound_wss_and_loopback_agent(self):
        source = (ROOT / "app" / "tunnel_client.py").read_text(encoding="utf-8")
        self.assertIn('websockets.asyncio.client import connect', source)
        self.assertIn('http://127.0.0.1:8766', source)
        self.assertNotIn('0.0.0.0', source)
        self.assertIn('additional_headers={"Authorization": f"Bearer {token}"}', source)

    def test_remote_delete_remains_disabled_in_agent(self):
        source = (ROOT / "app" / "storage_agent.py").read_text(encoding="utf-8")
        self.assertIn('Remote media deletion is disabled', source)
        self.assertIn('raise HTTPException(\n        405,', source)


if __name__ == "__main__":
    unittest.main()
