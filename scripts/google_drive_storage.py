"""Google Drive helpers for Evervault.

On Windows/local development, credentials are read from
credentials/google_drive_token.json as before.

On hosted platforms, set GOOGLE_DRIVE_TOKEN_JSON to the complete JSON
contents of that authorized-user token file in the platform's secret
environment-variable settings. Never commit the token file or its contents.
"""

import json
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

BASE = Path(__file__).resolve().parent.parent
TOKEN = BASE / "credentials" / "google_drive_token.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE_FOLDER_NAME = "Evervault_Media"


def get_drive_service():
    """Build Drive API service from hosted env secret or local token file."""
    hosted_token_json = os.environ.get("GOOGLE_DRIVE_TOKEN_JSON", "").strip()

    if hosted_token_json:
        try:
            token_info = json.loads(hosted_token_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "GOOGLE_DRIVE_TOKEN_JSON is not valid JSON. Paste the complete "
                "contents of google_drive_token.json into the hosting secret setting."
            ) from exc

        credentials = Credentials.from_authorized_user_info(token_info, SCOPES)
        using_hosted_secret = True
    else:
        if not TOKEN.exists():
            raise FileNotFoundError(
                "Google Drive token missing. For local use, run "
                "scripts/connect_google_drive.py. For hosting, set the "
                "GOOGLE_DRIVE_TOKEN_JSON secret environment variable."
            )
        credentials = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        using_hosted_secret = False

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        # Persist refreshed credentials only in local development. Hosted
        # environment variables are managed by the hosting platform.
        if not using_hosted_secret:
            TOKEN.write_text(credentials.to_json(), encoding="utf-8")

    if not credentials.valid:
        raise RuntimeError(
            "Google Drive credentials are invalid or expired. Reconnect the "
            "Google account locally or renew the hosted token secret."
        )

    return build("drive", "v3", credentials=credentials)


def get_media_folder_id(service):
    """Find or create the Evervault media folder."""
    query = (
        "name = '" + DRIVE_FOLDER_NAME + "' "
        "and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    result = service.files().list(
        q=query,
        pageSize=100,
        fields="files(id,name)",
    ).execute()
    folders = result.get("files", [])

    if folders:
        return folders[0]["id"]

    folder = service.files().create(
        body={
            "name": DRIVE_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        },
        fields="id",
    ).execute()
    return folder["id"]


def upload_file(local_path, drive_name=None, mime_type=None):
    """Upload a local file into Evervault_Media and return Drive metadata."""
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    service = get_drive_service()
    folder_id = get_media_folder_id(service)
    metadata = {
        "name": drive_name or path.name,
        "parents": [folder_id],
    }
    media = MediaFileUpload(
        str(path),
        mimetype=mime_type or "application/octet-stream",
        resumable=True,
    )
    return service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,size",
    ).execute()


def download_file(file_id, destination):
    """Download a Google Drive file to a local path."""
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("wb") as output:
        downloader = MediaIoBaseDownload(output, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return destination


def delete_file(file_id):
    """Delete a file from Google Drive."""
    service = get_drive_service()
    service.files().delete(fileId=file_id).execute()
    return True


def list_files():
    """List files inside the Evervault media folder."""
    service = get_drive_service()
    folder_id = get_media_folder_id(service)
    result = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        pageSize=100,
        orderBy="name",
        fields="files(id,name,mimeType,size)",
    ).execute()
    return result.get("files", [])


def test_connection():
    service = get_drive_service()
    result = service.about().get(fields="user").execute()
    user = result.get("user", {})
    print("Evervault Google Drive connection successful!")
    print("Connected account:", user.get("emailAddress", "Unknown"))


if __name__ == "__main__":
    test_connection()
