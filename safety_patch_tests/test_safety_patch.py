"""Safe, local-only checks for the Evervault remote-access safety patch.

Run from the project root with:
  .venv\\Scripts\\python.exe -m unittest discover -s tests -v

The storage-agent test sandbox is created under D:\\EvervaultState on Windows,
not on C:. Override EVERVAULT_TEST_TEMP_ROOT only if you intentionally want a
separate test location.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TOKEN = "unit-test-token-never-use-for-real-storage"


class EvervaultSafetyPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_env = {}
        temp_root_value = os.environ.get(
            "EVERVAULT_TEST_TEMP_ROOT",
            r"D:\EvervaultState" if os.name == "nt" else tempfile.gettempdir(),
        )
        temp_root = Path(temp_root_value)
        temp_root.mkdir(parents=True, exist_ok=True)
        cls._tmp = tempfile.TemporaryDirectory(prefix="evervault-safe-agent-test-", dir=str(temp_root))
        cls.root = Path(cls._tmp.name)
        cls.state_dir = cls.root / "state"
        cls.media_dir = cls.root / "media"
        cls.state_dir.mkdir()
        cls.media_dir.mkdir()
        cls.token_file = cls.state_dir / "storage_agent.token"
        cls.token_file.write_text(TEST_TOKEN, encoding="utf-8")

        overrides = {
            "EVERVAULT_STATE_DIR": str(cls.state_dir),
            "EVERVAULT_MEDIA_DIR": str(cls.media_dir),
            "EVERVAULT_AGENT_TOKEN_FILE": str(cls.token_file),
        }
        for key, value in overrides.items():
            cls._old_env[key] = os.environ.get(key)
            os.environ[key] = value

        sys.path.insert(0, str(PROJECT_ROOT))
        importlib.invalidate_caches()
        sys.modules.pop("app.storage_agent", None)
        cls.agent = importlib.import_module("app.storage_agent")
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.agent.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        sys.modules.pop("app.storage_agent", None)
        for key, old_value in cls._old_env.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
        cls._tmp.cleanup()

    def auth_headers(self):
        return {"Authorization": f"Bearer {TEST_TOKEN}"}

    def test_agent_requires_authentication(self):
        response = self.client.get("/internal/status")
        self.assertEqual(response.status_code, 401)

    def test_authenticated_status_reports_local_drive(self):
        response = self.client.get("/internal/status", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["media_drive_online"])

    def test_put_and_get_still_work_with_size_and_sha256_verification(self):
        stored_name = "safety-test-photo.bin"
        payload = b"Evervault local-agent safety test file"
        expected_hash = hashlib.sha256(payload).hexdigest()
        headers = {
            **self.auth_headers(),
            "X-Evervault-Size": str(len(payload)),
            "X-Evervault-SHA256": expected_hash,
            "Content-Type": "application/octet-stream",
        }
        response = self.client.put(f"/internal/files/{stored_name}", headers=headers, content=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sha256"], expected_hash)
        self.assertEqual((self.media_dir / stored_name).read_bytes(), payload)

        read_response = self.client.get(f"/internal/files/{stored_name}", headers=self.auth_headers())
        self.assertEqual(read_response.status_code, 200, read_response.text)
        self.assertEqual(read_response.content, payload)

    def test_delete_is_disabled_and_file_is_preserved(self):
        stored_name = "preserve-me.bin"
        target = self.media_dir / stored_name
        target.write_bytes(b"must remain untouched")
        response = self.client.delete(f"/internal/files/{stored_name}", headers=self.auth_headers())
        self.assertEqual(response.status_code, 405, response.text)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), b"must remain untouched")

    def test_invalid_filename_is_rejected(self):
        response = self.client.get("/internal/files/bad..name", headers=self.auth_headers())
        self.assertEqual(response.status_code, 400, response.text)

    def test_agent_openapi_schema_is_not_published(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 404)

    def test_website_delete_handler_has_no_storage_or_database_side_effects(self):
        tree = ast.parse((PROJECT_ROOT / "app" / "server.py").read_text(encoding="utf-8"))
        handler = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "delete_media"
        )
        called_names = set()
        # Inspect the function body only; the route decorator is expected to be @app.delete.
        for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    called_names.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    called_names.add(fn.attr)
        self.assertNotIn("db", called_names)
        self.assertNotIn("unlink", called_names)
        self.assertNotIn("delete", called_names)
        self.assertNotIn("get_drive_service", called_names)
        self.assertTrue(
            any(isinstance(node, ast.Raise) for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))),
            "delete_media should fail closed before doing any I/O",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
