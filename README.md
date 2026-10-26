# Evervault — Personal Media Cloud

A personal photo and media library with a FastAPI backend, a lightweight web interface, Google Drive file storage, and PostgreSQL-backed metadata for the hosted version.

**Hosted app:** [evervault-94v1.onrender.com](https://evervault-94v1.onrender.com)
**Repository:** [shuvadeepbiswas731-source/evervault](https://github.com/shuvadeepbiswas731-source/evervault)

> **Project status:** The hosted version is running. Local-first storage with Google Drive fallback and verified recovery is experimental and is not yet a supported, fully-tested feature. Do not use any cleanup or move script on important files until you have tested it with copies.

## What it does

- Upload and view photos and other media through a browser.
- Organize media into albums and mark favorites.
- Search the library and open stored files from the web interface.
- Use Google Drive to store the media files instead of relying on an ephemeral application filesystem.
- Store hosted app metadata and account settings in Neon PostgreSQL; the Windows/local version uses SQLite.
- Run the FastAPI application locally on Windows or deploy it as a web service on Render.

Evervault is designed primarily as a personal/single-owner library, not as a multi-tenant service for unrelated users.

## How the storage fits together

### Hosted edition

- **Render:** runs the FastAPI web application. The free service may sleep while idle.
- **Neon PostgreSQL:** stores metadata and account settings.
- **Google Drive:** stores media files through the Google Drive API.

Render cannot directly access files on your Windows computer or its `G:` drive. Online uploads therefore use the configured Google Drive account.

### Windows edition

- **Application:** run from the project folder.
- **SQLite and app state:** the configured `EVERVAULT_DATA_DIR` location; `start_windows.bat` currently displays `G:\EvervaultData` by default in the setup used by the maintainer.
- **Media files:** local storage is used by the local-first development version when the configured media drive is available.

Local-first fallback/reconciliation is still being developed. The intended workflow is to save locally when possible, use Google Drive only as a fallback if the local media drive is unavailable, and remove the fallback copy only after verifying the restored local file by size and SHA-256. Treat this workflow as experimental until the restore and failure cases have been tested end-to-end.

## Run locally on Windows

### Requirements

- Windows
- Python 3 installed and available as `py`
- A Google Cloud project with the Google Drive API enabled, plus an OAuth client configured for the application

### Start the app

1. Clone the repository.
2. Open Command Prompt in the project directory.
3. Create and install the environment:

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

4. Configure Google Drive credentials as described below.
5. Run:

   ```bat
   start_windows.bat
   ```

6. Open [http://localhost:8765](http://localhost:8765).

The project’s launcher and configured data directory may be specific to the maintainer’s Windows setup; review `start_windows.bat` before using it on a different machine.

## Deploy to Render + Neon

The currently hosted setup uses Render for the FastAPI process, Neon for PostgreSQL, and Google Drive for media files.

Typical Render settings:

- **Root directory:** blank
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn run_server:app --host 0.0.0.0 --port $PORT`

Configure these environment variables in the Render dashboard (do not commit real values):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection URL, including the required SSL setting |
| `EVERVAULT_SECRET` | Stable secret used by the application for signing sessions/cookies |
| `GOOGLE_DRIVE_TOKEN_JSON` | Complete OAuth token JSON used by the Drive storage helper |

For `GOOGLE_DRIVE_TOKEN_JSON`, supply the full JSON value from a freshly authorized token, including its opening and closing braces. Never publish the token, refresh token, client secret, database URL, or `EVERVAULT_SECRET`.

The app’s media metadata must already be present in the configured database. The migration utility `migrate_sqlite_to_neon.py` is a one-time maintainer tool and is intentionally excluded from source control; do not run migrations against production without a backup and a verification plan.

## Google Drive setup

1. Create or select a Google Cloud project.
2. Enable the Google Drive API.
3. Configure OAuth consent and create an OAuth client appropriate for the local authorization flow.
4. Authorize the Google account that should own the media.
5. Keep token files and OAuth client secrets outside Git. For Render, store the token JSON as a dashboard environment variable.

The Google account connected to the token is the account that receives media uploads. Check the authorized account before using the app with personal files.

## Repository map

```text
app/
  server.py               FastAPI app and API routes
  web/                    Browser UI assets
scripts/
  google_drive_storage.py Google Drive storage helper
android/                  Android/Capacitor companion project and backup plugin
run_server.py             ASGI app entry point
start_windows.bat         Windows launcher
requirements.txt           Python dependencies
```

There are also maintainer notes for Android background backup and migration. Scripts that move or delete stored files should be reviewed carefully before use.

## Security and privacy

- Never commit `.env`, OAuth credentials, Google Drive token JSON, `.secret`, SQLite databases, personal photos, or copied production data.
- Keep this repository private until you have reviewed the complete Git history and decided on a license.
- If a token or password is shared accidentally, revoke/rotate it at the provider and update the local configuration and Render environment variables.
- Use a dedicated Google account if you want to isolate app media from other personal Drive content.
- A public repository does not make the hosted library safe for public access by itself. Keep authentication and production secrets configured correctly.
- Back up your database and media before testing migration or cleanup utilities.

See [SECURITY.md](SECURITY.md) for reporting a security issue and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance.

## Roadmap

- [x] Hosted FastAPI app
- [x] Neon PostgreSQL for hosted metadata
- [x] Google Drive-backed media storage
- [ ] Fully tested local-first uploads and Google Drive fallback
- [ ] Safe recovery workflow that verifies restored files before cleanup
- [ ] More automated tests for disconnects, interrupted uploads, and restore failures
- [ ] Screenshots and a short contributor/developer walkthrough

## Contributing

Bug reports, documentation improvements, and focused pull requests are welcome. Please do not include real credentials, private photos, database dumps, access tokens, or other personal data in issues or pull requests.
