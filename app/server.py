
from __future__ import annotations

import os
import sys
import sqlite3
import secrets
import hashlib
import hmac
import time
import mimetypes
import uuid
import tempfile
import shutil
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Header,
    Request,
    WebSocket,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from urllib.parse import quote
import requests
import io
import uvicorn

from .tunnel import TUNNEL_MANAGER, TunnelUnavailable, bearer_matches


# --------------------------------------------------
# PATHS AND CONFIGURATION
# --------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
WEB_DIR = APP_DIR / "web"

# Detect Render PR previews before any optional .env loading.
IS_PULL_REQUEST = os.environ.get("IS_PULL_REQUEST", "").strip().lower() == "true"

# The Windows launcher may explicitly opt into loading its private .env file.
# Render continues to use dashboard environment variables and does not load .env.
if not IS_PULL_REQUEST and os.environ.get("EVERVAULT_LOAD_DOTENV", "").strip() == "1":
    try:
        from dotenv import load_dotenv
        env_file = os.environ.get("EVERVAULT_ENV_FILE", str(BASE_DIR / ".env"))
        load_dotenv(dotenv_path=env_file, override=False)
    except ImportError as exc:
        raise RuntimeError("python-dotenv is required when EVERVAULT_LOAD_DOTENV=1") from exc

# Isolate Render PR previews from production configuration.
if IS_PULL_REQUEST:
    DATABASE_URL = os.environ.get("EVERVAULT_PREVIEW_DATABASE_URL", "").strip()
    LOCAL_MEDIA_PRIMARY = False
    LOCAL_STORAGE_URL = ""
    # Only explicitly configured preview credentials may enable this tunnel.
    LOCAL_STORAGE_TOKEN = os.environ.get("EVERVAULT_PREVIEW_TUNNEL_TOKEN", "").strip()
    TUNNEL_ENABLED = (
        os.environ.get("EVERVAULT_PREVIEW_TUNNEL_ENABLED", "0").strip() == "1"
        and len(LOCAL_STORAGE_TOKEN) >= 32
    )
    _configured_media_root = ""
    _legacy_data_dir = Path("/tmp/evervault-pr-preview").absolute()
    _state_override = str(_legacy_data_dir)
    _media_override = ""
    _temp_override = ""
else:
    DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
    LOCAL_MEDIA_PRIMARY = os.environ.get("EVERVAULT_LOCAL_PRIMARY", "").strip() == "1"
    LOCAL_STORAGE_URL = os.environ.get("LOCAL_STORAGE_URL", "").strip().rstrip("/")
    LOCAL_STORAGE_TOKEN = os.environ.get("LOCAL_STORAGE_TOKEN", "").strip()
    TUNNEL_ENABLED = os.environ.get("EVERVAULT_TUNNEL_ENABLED", "0").strip() == "1"
    _configured_media_root = os.environ.get("EVERVAULT_DATA_DIR", "").strip()
    _legacy_data_dir = Path(
        _configured_media_root or str(Path.home() / "EvervaultData")
    ).expanduser().absolute()
    _state_override = os.environ.get("EVERVAULT_STATE_DIR", "").strip()
    _media_override = os.environ.get("EVERVAULT_MEDIA_DIR", "").strip()
    _temp_override = os.environ.get("EVERVAULT_TEMP_DIR", "").strip()

USE_POSTGRES = bool(DATABASE_URL)

# EVERVAULT_DATA_DIR is the preferred media root on Windows.
# State and temporary upload files must remain on an always-available drive.


if USE_POSTGRES:
    # Hosted uploads use ephemeral temp files plus Google Drive, with metadata in Neon.
    DATA_DIR = Path(_state_override or str(Path.home() / "EvervaultData")).expanduser().resolve()
    MEDIA_DIR = Path(_media_override or str(DATA_DIR / "media")).expanduser().absolute()
else:
    if _state_override:
        DATA_DIR = Path(_state_override).expanduser().resolve()
    elif os.name == "nt" and _configured_media_root:
        # Existing Windows launcher points EVERVAULT_DATA_DIR at the media drive.
        _local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        DATA_DIR = (_local_app_data / "EvervaultState").resolve()
    else:
        DATA_DIR = _legacy_data_dir

    if _media_override:
        MEDIA_DIR = Path(_media_override).expanduser().absolute()
    elif _configured_media_root:
        MEDIA_DIR = (_legacy_data_dir / "media").absolute()
    else:
        MEDIA_DIR = (DATA_DIR / "media").absolute()

TEMP_DIR = Path(_temp_override or str(DATA_DIR / "incoming")).expanduser().resolve()
DB_PATH = DATA_DIR / "evervault.sqlite3"

# The system state folder and upload staging must be available even if G: is offline.
DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
try:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    # Do not prevent server startup: local media writes will fail safely and use Drive.
    print(f"WARNING: local media drive is unavailable ({type(exc).__name__}); uploads will use Google Drive fallback.")

SECRET_FILE = DATA_DIR / ".secret"

# A PR preview must never reuse the production session secret.
if IS_PULL_REQUEST:
    _secret_from_env = os.environ.get("EVERVAULT_PREVIEW_SECRET", "").strip()
else:
    _secret_from_env = os.environ.get("EVERVAULT_SECRET", "").strip()
if _secret_from_env:
    SECRET = _secret_from_env.encode()
else:
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(48), encoding="utf-8")
    SECRET = SECRET_FILE.read_text(encoding="utf-8").strip().encode()


# Allow importing the Google Drive helper from scripts/
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from scripts.google_drive_storage import get_drive_service, get_media_folder_id
except ImportError as exc:
    raise RuntimeError(
        "Could not import scripts/google_drive_storage.py. "
        "Check that the file exists and contains get_drive_service()."
    ) from exc


app = FastAPI(
    title="Evervault Media",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Keep PR previews read-only over HTTP so inherited integrations cannot be used
# to upload, alter, or delete data during tunnel testing.
if IS_PULL_REQUEST:
    @app.middleware("http")
    async def preview_read_only_guard(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and not (
                request.method == "POST"
                and request.url.path in {"/api/setup", "/api/login"}
            )
        ):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=403,
                content={"detail": "PR preview is read-only"},
            )
        return await call_next(request)

    @app.get("/internal/tunnel/test_transfer")
    async def preview_tunnel_test_transfer():
        from fastapi.responses import JSONResponse
        if not TUNNEL_ENABLED:
            return JSONResponse(status_code=503, content={"ok": False, "error": "Tunnel disabled"})
        if not TUNNEL_MANAGER.connected:
            return JSONResponse(status_code=503, content={"ok": False, "error": "Tunnel client not connected"})

        test_data = b"Evervault reverse tunnel verified end-to-end payload: " + secrets.token_bytes(4096)
        expected_size = len(test_data)
        expected_sha256 = hashlib.sha256(test_data).hexdigest()
        test_stored_name = f"preview_test_{uuid.uuid4().hex[:12]}.bin"

        with tempfile.NamedTemporaryFile(prefix="tunnel-test-", suffix=".bin", delete=False) as tf:
            tf.write(test_data)
            tf_path = Path(tf.name)

        try:
            upload_result = await TUNNEL_MANAGER.upload_file(
                tf_path, test_stored_name, "application/octet-stream", expected_size, expected_sha256
            )
            if not upload_result or not isinstance(upload_result, dict):
                return JSONResponse(status_code=502, content={"ok": False, "error": "Upload via tunnel failed"})

            status_code, headers, body = await TUNNEL_MANAGER.open_download(test_stored_name)
            if status_code != 200 or body is None:
                return JSONResponse(status_code=502, content={"ok": False, "error": f"Download via tunnel returned status {status_code}"})

            downloaded = bytearray()
            async for chunk in body:
                downloaded.extend(chunk)

            download_sha256 = hashlib.sha256(downloaded).hexdigest()
            if len(downloaded) != expected_size or download_sha256 != expected_sha256:
                return JSONResponse(status_code=502, content={
                    "ok": False,
                    "error": "Checksum mismatch on download",
                    "expected_size": expected_size,
                    "downloaded_size": len(downloaded),
                    "expected_sha256": expected_sha256,
                    "downloaded_sha256": download_sha256,
                })

            return {
                "ok": True,
                "stored_name": test_stored_name,
                "size": expected_size,
                "sha256": expected_sha256,
                "upload_verified": True,
                "download_verified": True,
            }
        finally:
            tf_path.unlink(missing_ok=True)



@app.websocket("/internal/tunnel/ws")
async def evervault_tunnel_websocket(websocket: WebSocket):
    """Accept the one authenticated Windows client that initiates the tunnel."""
    if not TUNNEL_ENABLED:
        await websocket.close(code=4404, reason="Evervault tunnel is disabled")
        return
    if not bearer_matches(websocket.headers.get("authorization"), LOCAL_STORAGE_TOKEN):
        await websocket.close(code=4401, reason="Unauthorized")
        return
    await websocket.accept()
    await TUNNEL_MANAGER.serve_connection(websocket)


def local_agent_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    if not LOCAL_STORAGE_TOKEN:
        raise RuntimeError("LOCAL_STORAGE_TOKEN is not configured")
    headers = {"Authorization": f"Bearer {LOCAL_STORAGE_TOKEN}"}
    if extra:
        headers.update(extra)
    return headers


def try_store_via_local_agent(source: Path, stored_name: str, mime: str, size: int, sha256_hex: str) -> bool:
    """Try local storage through the outbound tunnel, verifying the agent's response.

    In tunnel mode, the public Render service never connects directly to the PC's
    loopback HTTP port. If the client is unavailable, the caller may use Drive fallback.
    """
    if TUNNEL_ENABLED:
        if not LOCAL_STORAGE_TOKEN:
            return False
        payload = TUNNEL_MANAGER.upload_file_blocking(source, stored_name, mime, size, sha256_hex)
        if not isinstance(payload, dict):
            print("Evervault tunnel upload unavailable; using Drive fallback.")
            return False
        try:
            return (
                int(payload.get("size", -1)) == int(size)
                and str(payload.get("sha256", "")).lower() == sha256_hex.lower()
                and payload.get("stored_name") == stored_name
            )
        except (TypeError, ValueError):
            return False

    # Compatibility path for the earlier direct HTTP agent; remove LOCAL_STORAGE_URL
    # from Render when tunnel mode is enabled.
    if not LOCAL_STORAGE_URL or not LOCAL_STORAGE_TOKEN:
        return False
    endpoint = f"{LOCAL_STORAGE_URL}/internal/files/{quote(stored_name, safe='')}"
    headers = local_agent_headers({
        "Content-Type": mime or "application/octet-stream",
        "X-Evervault-Size": str(size),
        "X-Evervault-SHA256": sha256_hex,
    })
    try:
        with source.open("rb") as stream:
            response = requests.put(endpoint, headers=headers, data=stream, timeout=(5, 300))
        try:
            if response.status_code != 200:
                print(f"Local storage agent upload unavailable (HTTP {response.status_code}); using Drive fallback.")
                return False
            payload = response.json()
            return (
                int(payload.get("size", -1)) == int(size)
                and str(payload.get("sha256", "")).lower() == sha256_hex.lower()
                and payload.get("stored_name") == stored_name
            )
        finally:
            response.close()
    except Exception as exc:
        # Deliberately log only the exception class; don't leak URLs or credentials.
        print(f"Local storage agent upload unavailable ({type(exc).__name__}); using Drive fallback.")
        return False



# --------------------------------------------------
# DATABASE AND MIGRATION
# --------------------------------------------------

class PostgresConnectionAdapter:
    """Small compatibility adapter for the SQLite-style SQL used by this app."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, parameters=()):
        # Application queries use SQLite's ? placeholders. psycopg uses %s.
        return self._connection.execute(sql.replace("?", "%s"), parameters or ())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()
        return False


def db():
    if USE_POSTGRES:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL is configured but psycopg is not installed. "
                "Install dependencies from requirements.txt."
            ) from exc
        connection = psycopg.connect(
            DATABASE_URL,
            connect_timeout=15,
            row_factory=dict_row,
        )
        return PostgresConnectionAdapter(connection)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def initialize_database():
    if USE_POSTGRES:
        # Tables are intentionally compatible with the completed SQLite migration.
        with db() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    size BIGINT NOT NULL,
                    created DOUBLE PRECISION NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    album TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL DEFAULT '',
                    drive_file_id TEXT
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    size BIGINT NOT NULL,
                    \"offset\" BIGINT NOT NULL DEFAULT 0,
                    temp_name TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    created DOUBLE PRECISION NOT NULL
                )
            """)
            con.execute("ALTER TABLE media ADD COLUMN IF NOT EXISTS drive_file_id TEXT")
            con.execute(
                "INSERT INTO settings(key,value) VALUES('password_hash','') "
                "ON CONFLICT(key) DO NOTHING"
            )
            con.execute(
                "INSERT INTO settings(key,value) VALUES('username','admin') "
                "ON CONFLICT(key) DO NOTHING"
            )
        return

    # Local Windows mode: retain SQLite and the existing data directory.
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                mime TEXT NOT NULL,
                size INTEGER NOT NULL,
                created REAL NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0,
                album TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT ''
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                size INTEGER NOT NULL,
                \"offset\" INTEGER NOT NULL DEFAULT 0,
                temp_name TEXT NOT NULL,
                mime TEXT NOT NULL,
                created REAL NOT NULL
            )
        """)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(media)").fetchall()}
        if "drive_file_id" not in columns:
            con.execute("ALTER TABLE media ADD COLUMN drive_file_id TEXT")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('password_hash','')")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('username','admin')")


initialize_database()

# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------

def password_hash(password: str, salt: Optional[bytes] = None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, saved: str):
    try:
        salt_hex, digest_hex = saved.split(":", 1)
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=2**14,
            r=8,
            p=1,
        ).hex()
        return hmac.compare_digest(actual, digest_hex)
    except Exception:
        return False


def setting(key):
    with db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()

    return row["value"] if row else ""


def token_for(username):
    payload = f"{username}:{int(time.time()) + 60 * 60 * 24 * 14}"
    sig = hmac.new(
        SECRET,
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    return (payload + ":" + sig).encode().hex()


def check_token(token):
    try:
        payload = bytes.fromhex(token).decode()
        username, expiry, sig = payload.rsplit(":", 2)

        expected = hmac.new(
            SECRET,
            f"{username}:{expiry}".encode(),
            hashlib.sha256,
        ).hexdigest()

        if int(expiry) < time.time():
            return None

        if not hmac.compare_digest(sig, expected):
            return None

        if username != setting("username"):
            return None

        return username

    except Exception:
        return None


def auth(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in")

    user = check_token(authorization[7:])

    if not user:
        raise HTTPException(
            401,
            "Session expired. Please sign in again.",
        )

    return user


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class SetupBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class FavoriteBody(BaseModel):
    favorite: bool


class AlbumBody(BaseModel):
    album: str


# --------------------------------------------------
# STATUS, SETUP AND LOGIN
# --------------------------------------------------

@app.get("/api/status")
def status():
    return {
        "setup_required": not bool(setting("password_hash")),
        "app": "Evervault Media",
    }


@app.post("/api/setup")
def setup(body: SetupBody):
    if setting("password_hash"):
        raise HTTPException(
            403,
            "Setup has already been completed",
        )

    username = body.username.strip()

    if (
        len(username) < 3
        or len(username) > 40
        or not username.replace("_", "").replace("-", "").isalnum()
    ):
        raise HTTPException(
            400,
            "Username must be 3–40 letters, numbers, underscores or hyphens",
        )

    if len(body.password) < 12:
        raise HTTPException(
            400,
            "Use a password with at least 12 characters",
        )

    with db() as con:
        con.execute(
            "UPDATE settings SET value=? WHERE key='username'",
            (username,),
        )
        con.execute(
            "UPDATE settings SET value=? WHERE key='password_hash'",
            (password_hash(body.password),),
        )

    return {
        "token": token_for(username),
        "username": username,
    }


@app.post("/api/login")
def login(body: LoginBody):
    if not setting("password_hash"):
        raise HTTPException(400, "Complete first-time setup")

    if (
        body.username != setting("username")
        or not verify_password(
            body.password,
            setting("password_hash"),
        )
    ):
        time.sleep(0.4)
        raise HTTPException(401, "Incorrect username or password")

    return {
        "token": token_for(body.username),
        "username": body.username,
    }


@app.get("/api/me")
def me(user=Depends(auth)):
    return {"username": user}


# --------------------------------------------------
# MEDIA LIST AND ALBUMS
# --------------------------------------------------

@app.get("/api/media")
def list_media(
    q: str = "",
    album: str = "",
    favorites: bool = False,
    user=Depends(auth),
):
    sql = (
        "SELECT id,name,mime,size,created,favorite,album "
        "FROM media WHERE 1=1"
    )
    args = []

    if q.strip():
        sql += " AND name LIKE ?"
        args.append("%" + q.strip().replace("%", "") + "%")

    if album:
        sql += " AND album=?"
        args.append(album)

    if favorites:
        sql += " AND favorite=1"

    sql += " ORDER BY created DESC"

    with db() as con:
        rows = con.execute(sql, args).fetchall()

    return {"items": [dict(row) for row in rows]}


@app.get("/api/albums")
def albums(user=Depends(auth)):
    with db() as con:
        rows = con.execute("""
            SELECT album, COUNT(*) count
            FROM media
            WHERE album <> ''
            GROUP BY album
            ORDER BY album
        """).fetchall()

    return {"items": [dict(row) for row in rows]}


# --------------------------------------------------
# CHUNKED UPLOAD: START
# --------------------------------------------------

@app.post("/api/uploads")
def start_upload(body: dict, user=Depends(auth)):
    name = Path(str(body.get("name", "upload.bin"))).name

    try:
        size = int(body.get("size", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid file size")

    mime = str(
        body.get("mime")
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )

    if size <= 0 or size > 100 * 1024**3:
        raise HTTPException(
            400,
            "File must be between 1 byte and 100 GB",
        )

    uid = uuid.uuid4().hex
    tmp = uid + ".part"
    (TEMP_DIR / tmp).touch()

    with db() as con:
        con.execute(
            """
            INSERT INTO uploads
            (id,name,size,"offset",temp_name,mime,created)
            VALUES(?,?,?,?,?,?,?)
            """,
            (uid, name, size, 0, tmp, mime, time.time()),
        )

    return {
        "upload_id": uid,
        "offset": 0,
        "chunk_size": 8 * 1024 * 1024,
    }


# --------------------------------------------------
# CHUNKED UPLOAD: RECEIVE CHUNK
# --------------------------------------------------

@app.put("/api/uploads/{upload_id}")
async def upload_chunk(
    upload_id: str,
    request: Request,
    offset: int,
    user=Depends(auth),
):
    with db() as con:
        row = con.execute(
            "SELECT * FROM uploads WHERE id=?",
            (upload_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, "Upload session not found")

    if offset != row["offset"]:
        raise HTTPException(
            409,
            f"Expected offset {row['offset']}",
        )

    dest = TEMP_DIR / row["temp_name"]
    chunk = await request.body()

    if not chunk:
        raise HTTPException(400, "Empty chunk")

    if row["offset"] + len(chunk) > row["size"]:
        raise HTTPException(413, "Chunk exceeds declared file size")

    with dest.open("ab") as file:
        file.write(chunk)

    new_offset = row["offset"] + len(chunk)

    with db() as con:
        con.execute(
            'UPDATE uploads SET \"offset\"=? WHERE id=?',
            (new_offset, upload_id),
        )

    return {
        "offset": new_offset,
        "complete": new_offset == row["size"],
    }


# --------------------------------------------------
# CHUNKED UPLOAD: FINISH AND STORE IN GOOGLE DRIVE
# --------------------------------------------------

@app.post("/api/uploads/{upload_id}/finish")
def finish_upload(upload_id: str, user=Depends(auth)):
    with db() as con:
        row = con.execute(
            "SELECT * FROM uploads WHERE id=?",
            (upload_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, "Upload session not found")

    if row["offset"] != row["size"]:
        raise HTTPException(
            409,
            f"Upload incomplete: {row['offset']} of {row['size']} bytes",
        )

    tmp = TEMP_DIR / row["temp_name"]
    if not tmp.is_file():
        raise HTTPException(500, "Temporary upload file is missing")

    def hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    sha_hex = hash_file(tmp)
    mid = uuid.uuid4().hex
    suffix = Path(row["name"]).suffix[:16]
    stored = mid + suffix
    local_path = MEDIA_DIR / stored
    drive_file_id = None
    storage = "local"
    copied_locally = False
    stored_by_agent = False

    # Windows local mode writes G: first, even when Neon is the shared metadata DB.
    # Render cannot see the Windows disk; it asks the protected storage agent to do it.
    if LOCAL_MEDIA_PRIMARY or not USE_POSTGRES:
        try:
            if not MEDIA_DIR.is_dir():
                raise OSError("Configured local media drive is unavailable")
            shutil.copyfile(tmp, local_path)
            copied_locally = True
            if local_path.stat().st_size != row["size"] or hash_file(local_path) != sha_hex:
                raise OSError("Local copy failed size/SHA-256 verification")
            storage = "local"
        except OSError as exc:
            try:
                local_path.unlink(missing_ok=True)
            except OSError:
                pass
            copied_locally = False
            storage = "google_drive_fallback"
            print(f"Local media write failed ({type(exc).__name__}); using Google Drive fallback.")
    elif USE_POSTGRES:
        stored_by_agent = try_store_via_local_agent(
            tmp, stored, str(row["mime"]), int(row["size"]), sha_hex
        )
        if stored_by_agent:
            storage = "local_agent"
        else:
            storage = "google_drive_fallback"

    # Store in Google Drive only when the local storage agent/tunnel is not reachable.
    # A Drive-backed record remains a temporary recovery item in tunnel mode; the
    # hosted app reads media from G: through the connected Windows client.
    if not copied_locally and not stored_by_agent:
        try:
            service = get_drive_service()
            folder_id = get_media_folder_id(service)
            metadata = {
                "name": row["name"],
                "parents": [folder_id],
                "appProperties": {"evervault_media_id": mid, "evervault_storage_role": "fallback"},
            }
            with tmp.open("rb") as upload_file:
                media = MediaIoBaseUpload(
                    upload_file,
                    mimetype=row["mime"],
                    resumable=True,
                    chunksize=8 * 1024 * 1024,
                )
                uploaded = service.files().create(
                    body=metadata,
                    media_body=media,
                    fields="id,name,size",
                ).execute()
            drive_file_id = uploaded["id"]
            storage = "google_drive_fallback"
        except Exception as exc:
            # Keep source temp and upload session for retry if fallback storage fails.
            raise HTTPException(
                502,
                "Google Drive fallback upload failed; the temporary upload has been kept. "
                f"Details: {str(exc)[:200]}",
            ) from exc

    try:
        with db() as con:
            con.execute(
                """
                INSERT INTO media
                (id,name,stored_name,mime,size,created,sha256,drive_file_id)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    mid,
                    row["name"],
                    stored,
                    row["mime"],
                    row["size"],
                    time.time(),
                    sha_hex,
                    drive_file_id,
                ),
            )
            con.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
    except Exception as exc:
        # Preserve every completed storage copy if metadata commit fails.
        # Orphan reconciliation is safer than deleting user data during an uncertain transaction.
        retained = []
        if copied_locally:
            retained.append("G: copy")
        if stored_by_agent:
            retained.append("storage-agent copy")
        if drive_file_id:
            retained.append("Google Drive fallback")
        if retained:
            print("Media metadata commit failed; retained uploaded bytes for reconciliation: " + ", ".join(retained))
        raise HTTPException(
            500,
            "Could not save media metadata. Uploaded file copies were retained for recovery; "
            "reconcile storage before retrying this upload.",
        ) from exc

    tmp.unlink(missing_ok=True)
    return {"id": mid, "name": row["name"], "storage": storage}


# --------------------------------------------------
# UPLOAD STATUS
# --------------------------------------------------

@app.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str, user=Depends(auth)):
    with db() as con:
        row = con.execute(
            'SELECT id,name,size,\"offset\" FROM uploads WHERE id=?',
            (upload_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, "Upload session not found")

    return dict(row)


# --------------------------------------------------
# SERVE MEDIA FROM LOCAL DISK OR GOOGLE DRIVE
# --------------------------------------------------

@app.get("/api/media/{media_id}/file")
async def media_file(
    media_id: str,
    request: Request,
    token: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    # Native browser image/video elements cannot attach auth headers.
    user = check_token(token) if token else None

    if not user and authorization and authorization.startswith("Bearer "):
        user = check_token(authorization[7:])

    if not user:
        raise HTTPException(401, "Please sign in")

    with db() as con:
        row = con.execute(
            "SELECT * FROM media WHERE id=?",
            (media_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, "Media not found")

    drive_id = row["drive_file_id"]
    stored_name = Path(str(row["stored_name"])).name
    local_path = MEDIA_DIR / stored_name

    # Preferred remote path: Render relays the request over the Windows client's
    # outbound authenticated WebSocket. No inbound PC port or public local-agent URL is used.
    if USE_POSTGRES and TUNNEL_ENABLED:
        if not LOCAL_STORAGE_TOKEN:
            raise HTTPException(503, "The Evervault tunnel is not configured.")
        tunnel_status, tunnel_headers, tunnel_body = 503, {}, None
        try:
            tunnel_status, tunnel_headers, tunnel_body = await TUNNEL_MANAGER.open_download(
                stored_name, request.headers.get("range")
            )
        except (TunnelUnavailable, ValueError):
            # A disconnected PC or a photo not yet restored to G: should not make a
            # verified Google Drive fallback unreadable while its fallback ID remains.
            pass
        if tunnel_status in (200, 206) and tunnel_body is not None:
            headers = {
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(str(row['name']), safe='')}",
                "Accept-Ranges": tunnel_headers.get("accept-ranges", "bytes"),
            }
            for key in ("content-length", "content-range", "last-modified", "etag"):
                if key in tunnel_headers:
                    headers[{"content-length":"Content-Length", "content-range":"Content-Range", "last-modified":"Last-Modified", "etag":"ETag"}[key]] = tunnel_headers[key]
            return StreamingResponse(
                tunnel_body,
                status_code=tunnel_status,
                media_type=row["mime"],
                headers=headers,
            )
        # If no Drive fallback exists, fail closed; otherwise let the verified
        # drive_file_id fall through to the existing streamed Google Drive reader.
        if not drive_id:
            raise HTTPException(503, "The Evervault Windows client is not connected, and no cloud fallback is available.")

    # Compatibility path for earlier deployments that exposed an authenticated local-agent URL.
    if USE_POSTGRES and LOCAL_STORAGE_URL:
        if not LOCAL_STORAGE_TOKEN:
            raise HTTPException(503, "Local storage agent is not configured.")
        endpoint = f"{LOCAL_STORAGE_URL}/internal/files/{quote(stored_name, safe='')}"
        agent_headers = local_agent_headers()
        if "range" in request.headers:
            agent_headers["Range"] = request.headers["range"]
        try:
            remote = requests.get(endpoint, headers=agent_headers, stream=True, timeout=(5, 60))
            if remote.status_code != 200 and remote.status_code != 206:
                status_code = remote.status_code
                remote.close()
                raise HTTPException(
                    503,
                    "The personal photo drive is offline or this file is waiting for recovery. "
                    "Evervault will show the file again when the local drive is online and verified.",
                )
            headers = {
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(str(row['name']), safe='')}",
                "Accept-Ranges": remote.headers.get("Accept-Ranges", "bytes"),
            }
            for key in ("Content-Length", "Content-Range", "Last-Modified", "ETag"):
                if key in remote.headers:
                    headers[key] = remote.headers[key]
            def body_stream():
                try:
                    for chunk in remote.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            yield chunk
                finally:
                    remote.close()
            return StreamingResponse(
                body_stream(),
                status_code=remote.status_code,
                media_type=row["mime"],
                headers=headers,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                503,
                "The personal photo drive is currently unreachable. Try again when the PC and G: drive are online."
            ) from exc

    # Windows app uses G: directly; local-only mode must not stream cloud fallbacks as primary media.
    if local_path.is_file():
        return FileResponse(
            local_path,
            media_type=row["mime"],
            filename=row["name"],
            content_disposition_type="inline",
        )

    if LOCAL_MEDIA_PRIMARY:
        raise HTTPException(
            503,
            "This photo is waiting for recovery to the local G: drive. It will be readable again after verification.",
        )

    if not drive_id:
        raise HTTPException(
            503,
            "This media item is local-only, but the configured local storage is currently unavailable.",
        )

    # Compatibility path used only before a local storage agent has been configured in Render.
    temp_path = None
    try:
        service = get_drive_service()
        with tempfile.NamedTemporaryFile(
            prefix="evervault-",
            suffix=Path(row["name"]).suffix,
            delete=False,
            dir=str(TEMP_DIR),
        ) as temp_file:
            temp_path = Path(temp_file.name)
            drive_request = service.files().get_media(fileId=drive_id)
            downloader = MediaIoBaseDownload(temp_file, drive_request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return FileResponse(
            temp_path,
            media_type=row["mime"],
            filename=row["name"],
            content_disposition_type="inline",
            background=BackgroundTask(lambda: temp_path.unlink(missing_ok=True)),
        )
    except Exception as exc:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise HTTPException(502, "Could not retrieve this file from Google Drive.") from exc


# --------------------------------------------------
# FAVORITES AND ALBUMS
# --------------------------------------------------

@app.patch("/api/media/{media_id}/favorite")
def favorite(
    media_id: str,
    body: FavoriteBody,
    user=Depends(auth),
):
    with db() as con:
        cur = con.execute(
            "UPDATE media SET favorite=? WHERE id=?",
            (1 if body.favorite else 0, media_id),
        )

        if cur.rowcount == 0:
            raise HTTPException(404, "Media not found")

    return {"ok": True}


@app.patch("/api/media/{media_id}/album")
def set_album(
    media_id: str,
    body: AlbumBody,
    user=Depends(auth),
):
    album = body.album.strip()[:80]

    with db() as con:
        cur = con.execute(
            "UPDATE media SET album=? WHERE id=?",
            (album, media_id),
        )

        if cur.rowcount == 0:
            raise HTTPException(404, "Media not found")

    return {"ok": True}


# --------------------------------------------------
# DELETE MEDIA FROM ITS STORAGE LOCATION
# --------------------------------------------------

@app.delete("/api/media/{media_id}")
def delete_media(media_id: str, user=Depends(auth)):
    # Deletion stays disabled until storage, fallback, and metadata changes can be
    # coordinated and verified as one safe workflow. This must run before any I/O.
    raise HTTPException(
        405,
        "Media deletion is temporarily disabled while local-first recovery is being validated. "
        "No file or metadata was changed.",
    )


# --------------------------------------------------
# WEB APP
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


app.mount(
    "/assets",
    StaticFiles(directory=WEB_DIR / "assets"),
    name="assets",
)


# --------------------------------------------------
# START SERVER
# --------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("EVERVAULT_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", os.environ.get("EVERVAULT_PORT", "8765"))),
    )
