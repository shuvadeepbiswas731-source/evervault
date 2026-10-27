"""Minimal authenticated local-media service for Evervault.

Run on the Windows PC only, bound to 127.0.0.1:8766. Expose this service through
Tailscale Funnel only after setting LOCAL_STORAGE_TOKEN in Render. Do not expose
port 8765 or bind this service to 0.0.0.0.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

STATE_DIR = Path(os.environ.get("EVERVAULT_STATE_DIR", r"D:\EvervaultState")).expanduser()
MEDIA_DIR = Path(os.environ.get("EVERVAULT_MEDIA_DIR", r"G:\EvervaultData\media")).expanduser()
TOKEN_FILE = Path(os.environ.get("EVERVAULT_AGENT_TOKEN_FILE", str(STATE_DIR / "storage_agent.token"))).expanduser()
STAGING_DIR = STATE_DIR / "agent-staging"
# Restrict all requests to generated Evervault stored names (no paths, dots-only names, or separators).
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,239}$")

app = FastAPI(title="Evervault Storage Agent", docs_url=None, redoc_url=None, openapi_url=None)


def require_auth(authorization: str | None) -> None:
    if not TOKEN_FILE.is_file():
        raise HTTPException(503, "Storage agent token is not initialized.")
    expected = TOKEN_FILE.read_text(encoding="utf-8").strip()
    actual = (authorization or "")
    if actual.startswith("Bearer "):
        actual = actual[7:].strip()
    else:
        actual = ""
    if not expected or not hmac.compare_digest(actual, expected):
        raise HTTPException(401, "Unauthorized")


def valid_name(filename: str) -> str:
    if not SAFE_NAME.fullmatch(filename) or filename in {".", ".."} or ".." in filename:
        raise HTTPException(400, "Invalid stored filename")
    return filename


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), total


def drive_online() -> bool:
    # Do not create MEDIA_DIR here: a missing G: must never silently become a directory elsewhere.
    return MEDIA_DIR.is_dir()


@app.get("/internal/status")
def status(authorization: str | None = Header(default=None)):
    require_auth(authorization)
    online = drive_online()
    return {
        "ok": True,
        "media_drive_online": online,
        "stored_media_root_configured": str(MEDIA_DIR).startswith(("G:\\", "g:\\")) if os.name == "nt" else True,
    }


@app.get("/internal/files/{stored_name}")
def read_file(stored_name: str, authorization: str | None = Header(default=None)):
    require_auth(authorization)
    stored_name = valid_name(stored_name)
    if not drive_online():
        raise HTTPException(503, "Local media drive is offline")
    target = MEDIA_DIR / stored_name
    if not target.is_file():
        raise HTTPException(404, "Media file is not present on the local drive")
    return FileResponse(target, filename=stored_name, content_disposition_type="inline")


@app.put("/internal/files/{stored_name}")
async def write_file(
    stored_name: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_evervault_size: str | None = Header(default=None),
    x_evervault_sha256: str | None = Header(default=None),
):
    require_auth(authorization)
    stored_name = valid_name(stored_name)
    if not drive_online():
        raise HTTPException(503, "Local media drive is offline; upload should use the fallback")
    try:
        expected_size = int(x_evervault_size or "-1")
    except ValueError:
        raise HTTPException(400, "Invalid expected size")
    expected_hash = (x_evervault_sha256 or "").lower()
    if expected_size < 1 or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise HTTPException(400, "Expected size and SHA-256 are required")

    target = MEDIA_DIR / stored_name
    if target.is_file():
        existing_hash, existing_size = hash_file(target)
        if existing_size == expected_size and hmac.compare_digest(existing_hash, expected_hash):
            return {"ok": True, "stored_name": stored_name, "size": existing_size, "sha256": existing_hash, "already_present": True}
        raise HTTPException(409, "A different file already uses this stored name; nothing was overwritten")

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staged = STAGING_DIR / f"{uuid.uuid4().hex}.uploading"
    partial_on_media = MEDIA_DIR / f".{stored_name}.{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    total = 0
    try:
        with staged.open("xb") as output:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > expected_size:
                    raise HTTPException(413, "Request exceeded declared size")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        observed_hash = digest.hexdigest()
        if total != expected_size or not hmac.compare_digest(observed_hash, expected_hash):
            raise HTTPException(422, "Upload size or SHA-256 verification failed")
        # Recheck the removable drive after the request body is fully received.
        if not drive_online():
            raise HTTPException(503, "Local media drive went offline during upload")
        # Copy staged data onto G:, verify that copy, then atomically publish final name.
        shutil.copyfile(staged, partial_on_media)
        copied_hash, copied_size = hash_file(partial_on_media)
        if copied_size != expected_size or not hmac.compare_digest(copied_hash, expected_hash):
            raise HTTPException(500, "Final drive copy failed verification")
        if target.exists():
            # Avoid replacing a file that appeared concurrently.
            current_hash, current_size = hash_file(target)
            if current_size != expected_size or not hmac.compare_digest(current_hash, expected_hash):
                raise HTTPException(409, "A different file appeared at the destination; it was not overwritten")
            partial_on_media.unlink(missing_ok=True)
        else:
            partial_on_media.replace(target)
        final_hash, final_size = hash_file(target)
        if final_size != expected_size or not hmac.compare_digest(final_hash, expected_hash):
            raise HTTPException(500, "Published local file failed final verification")
        return {"ok": True, "stored_name": stored_name, "size": final_size, "sha256": final_hash, "already_present": False}
    finally:
        staged.unlink(missing_ok=True)
        # Keep only verified final files; unverified partial copies are safe to remove.
        partial_on_media.unlink(missing_ok=True)


@app.delete("/internal/files/{stored_name}")
def delete_file(stored_name: str, authorization: str | None = Header(default=None)):
    # Authenticate requests consistently, but disable destructive operations for
    # the first remote-access release. No path is opened and no file is removed.
    require_auth(authorization)
    valid_name(stored_name)
    raise HTTPException(
        405,
        "Remote media deletion is disabled until Evervault's verified recovery workflow is ready.",
    )
