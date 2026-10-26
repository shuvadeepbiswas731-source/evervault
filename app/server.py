
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
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Header,
    Request,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import io
import uvicorn


# --------------------------------------------------
# PATHS AND CONFIGURATION
# --------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
WEB_DIR = APP_DIR / "web"

DATA_DIR = Path(
    os.environ.get(
        "EVERVAULT_DATA_DIR",
        str(Path.home() / "EvervaultData"),
    )
).expanduser().resolve()

MEDIA_DIR = DATA_DIR / "media"
TEMP_DIR = DATA_DIR / "incoming"
DB_PATH = DATA_DIR / "evervault.sqlite3"

# Hosted deployments set DATABASE_URL in the Render environment.
# If it is absent, Windows continues to use the local SQLite database.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

SECRET_FILE = DATA_DIR / ".secret"

# Render should define EVERVAULT_SECRET so sessions remain valid across restarts.
# Windows keeps using the existing local .secret file when the variable is absent.
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
    from scripts.google_drive_storage import get_drive_service
except ImportError as exc:
    raise RuntimeError(
        "Could not import scripts/google_drive_storage.py. "
        "Check that the file exists and contains get_drive_service()."
    ) from exc


app = FastAPI(
    title="Evervault Media",
    docs_url=None,
    redoc_url=None,
)


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

    # Calculate SHA-256 before uploading.
    sha = hashlib.sha256()

    with tmp.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            sha.update(block)

    mid = uuid.uuid4().hex
    suffix = Path(row["name"]).suffix[:16]
    stored = mid + suffix

    # Upload the completed file to Google Drive.
    drive_file_id = None

    try:
        service = get_drive_service()

        metadata = {
            "name": row["name"],
            "appProperties": {
                "evervault_media_id": mid,
            },
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
                fields="id,name",
            ).execute()

        drive_file_id = uploaded["id"]

        # Save metadata only after Drive upload succeeds.
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
                    sha.hexdigest(),
                    drive_file_id,
                ),
            )

            con.execute(
                "DELETE FROM uploads WHERE id=?",
                (upload_id,),
            )

        # The original temporary upload is no longer needed.
        tmp.unlink(missing_ok=True)

        return {
            "id": mid,
            "name": row["name"],
            "storage": "google_drive",
        }

    except Exception as exc:
        # Keep the upload session and temp file if Drive fails,
        # allowing troubleshooting or a later retry.
        if isinstance(exc, HTTPException):
            raise

        raise HTTPException(
            502,
            "Google Drive upload failed. "
            "The temporary upload has been kept for recovery. "
            f"Details: {str(exc)[:300]}",
        ) from exc


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
def media_file(
    media_id: str,
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

    # Older media items continue to use local storage.
    if not drive_id:
        path = MEDIA_DIR / row["stored_name"]

        if not path.is_file():
            raise HTTPException(
                404,
                "Media file is missing from local storage",
            )

        return FileResponse(
            path,
            media_type=row["mime"],
            filename=row["name"],
            content_disposition_type="inline",
        )

    # Download Drive-backed media to a temporary file so large
    # videos are not loaded entirely into RAM.
    temp_path = None

    try:
        service = get_drive_service()

        # Stream the Drive response to disk in chunks so large videos do not
        # need to be loaded entirely into application memory.
        with tempfile.NamedTemporaryFile(
            prefix="evervault-",
            suffix=Path(row["name"]).suffix,
            delete=False,
            dir=str(TEMP_DIR),
        ) as temp_file:
            temp_path = Path(temp_file.name)
            request = service.files().get_media(fileId=drive_id)
            downloader = MediaIoBaseDownload(
                temp_file,
                request,
                chunksize=8 * 1024 * 1024,
            )
            done = False
            while not done:
                _, done = downloader.next_chunk()

        return FileResponse(
            temp_path,
            media_type=row["mime"],
            filename=row["name"],
            content_disposition_type="inline",
            background=BackgroundTask(
                lambda: temp_path.unlink(missing_ok=True)
            ),
        )

    except Exception as exc:
        if temp_path:
            temp_path.unlink(missing_ok=True)

        raise HTTPException(
            502,
            "Could not retrieve this file from Google Drive. "
            f"Details: {str(exc)[:250]}",
        ) from exc


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
    with db() as con:
        row = con.execute(
            "SELECT stored_name,drive_file_id FROM media WHERE id=?",
            (media_id,),
        ).fetchone()

    if not row:
        raise HTTPException(404, "Media not found")

    drive_id = row["drive_file_id"]

    if drive_id:
        # Delete the Drive file first. If that fails, preserve
        # the database record so the file is not orphaned silently.
        try:
            service = get_drive_service()
            service.files().delete(fileId=drive_id).execute()
        except Exception as exc:
            raise HTTPException(
                502,
                "Could not delete the file from Google Drive. "
                "The Evervault record has been kept. "
                f"Details: {str(exc)[:250]}",
            ) from exc
    else:
        # Existing files stored locally are deleted locally.
        (MEDIA_DIR / row["stored_name"]).unlink(missing_ok=True)

    with db() as con:
        con.execute(
            "DELETE FROM media WHERE id=?",
            (media_id,),
        )

    return {"ok": True}


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
