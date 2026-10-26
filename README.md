# Evervault Personal Media — self-hosted starter

A private, Google Photos-inspired personal media library. The Windows machine runs the server; original media files live on your own HDD. The responsive web UI can be installed as a PWA and wrapped into an Android APK with Capacitor.

## Included
- First-run owner account with a password of at least 12 characters; password hashes use scrypt.
- Authenticated gallery, search, video playback, favourites, albums, and delete endpoint.
- Chunked uploads (8 MiB chunks) with server-side offset checks and final SHA-256 verification.
- SQLite metadata and media stored outside the application directory.
- Windows launch and PyInstaller build scripts.
- Android Capacitor wrapper with an opt-in native background backup module (WorkManager source and integration instructions).
- Public HTTPS access setup notes.

## Important status / limitations
This is a runnable MVP starter, not yet a Google Photos feature-equivalent product. Uploads are chunked and can be retried by restarting a new upload, but automatic client-side resume after a browser closes, HEIC/RAW thumbnail generation, face recognition, duplicate detection, and transcoding are not included yet. An initial Android background backup module is included but requires native integration, APK build, and device testing. Browser video seeking depends on the browser/server range support. Albums are simple labels. The Android wrapper requires a configured HTTPS hostname and a local Android build; no signed APK is prebuilt in this source archive.

## Requirements
- Windows 10/11, 64-bit
- Python 3.11+ (check `py --version`)
- Optional: Node.js LTS + Android Studio to build the Android APK
- For remote access: Cloudflare account and `cloudflared`, or another HTTPS reverse tunnel you trust

## Run locally
1. Extract this folder to a stable path.
2. Double-click `start_windows.bat`. It creates a virtual environment and installs dependencies on first run.
3. Open `http://127.0.0.1:8765`.
4. On first launch, create the owner username and a long unique password.
5. Select **Add memories** to upload photos/videos.

The default data directory is `%USERPROFILE%\\EvervaultData`. It contains `media`, `incoming`, `evervault.sqlite3`, and the private signing secret. Back up this directory. Do not delete `.secret` while the server is running or existing sessions will be invalidated.

## Use your G: drive (configured for this PC)
The included `start_windows.bat` is configured to use `G:\EvervaultData`. Connect G: before starting the server.

If you already uploaded photos to C:, first close the running Evervault server window, then double-click `migrate_to_g_drive.bat`. It copies `%USERPROFILE%\EvervaultData` to `G:\EvervaultData`, preserves the original C: copy, and configures the drive path. Then launch `start_windows.bat` and confirm your existing photos still appear.

Do not delete the C: copy until you have verified the gallery and opened several photos/videos. Keep the drive letter stable. The launcher checks that G: exists before starting to avoid silently using the wrong drive.

## Build a Windows executable
Run `build_windows.bat`. The output is `dist\\Evervault\\Evervault.exe` plus its supporting `_internal` directory. Keep the whole `Evervault` output folder together. The program listens on port 8765. This build is a server executable; it opens a console window and you browse the gallery at `http://127.0.0.1:8765`. It does not yet install as a Windows service or tray application.

## Public remote access (Cloudflare Tunnel)
Do **not** expose port 8765 directly on your router. Use an HTTPS tunnel.

1. Install `cloudflared` on Windows and sign in to Cloudflare.
2. Create a Cloudflare Tunnel and attach a hostname you control, e.g. `photos.example.com`.
3. Configure the tunnel's local service as `http://localhost:8765`.
4. Start Evervault first, then run your tunnel. Use the exact HTTPS hostname for phone/other devices.
5. Test on mobile data with Wi-Fi disabled. Sign in with your Evervault username/password.

Cloudflare Tunnel encrypts the public leg and avoids inbound router port forwarding. A custom domain is optional only if your tunnel setup supports a managed `trycloudflare.com` URL; that URL can change and is not ideal for the Android wrapper. Never publish this app without a strong unique password. For additional protection, enable Cloudflare Access in front of the hostname and allow only your own identity. Note that Access login and the app login are separate layers.

## Security and reliability notes
- The first-run setup endpoint closes after an owner account is created.
- Keep the host OS patched, restrict who can access the hostname, and use a unique password.
- Do not expose the server's SQLite file or media directory through a static web server.
- Keep a second backup of irreplaceable photos; a single HDD is not a backup.
- The server binds to all interfaces on port 8765 for LAN/tunnel use. Do not port-forward that port.
- This MVP has no account recovery or multi-user support. Keep the password manager entry safe.
- Large upload sizes depend on disk space, tunnel limits, browser memory, and connection stability.

## Android app
See `android/README.md` and `android/native-background-backup/README.md`. The wrapper includes opt-in charging/network-constrained backup source code, but you must integrate the native plugin and build/test an APK with Node.js + Android Studio. If you prefer not to install a native app yet, open the HTTPS URL in Chrome on Android and use **Install app / Add to Home screen**.

## Troubleshooting
- Port busy: change `EVERVAULT_PORT` before launch (e.g. `setx EVERVAULT_PORT 8766`) and update the tunnel target.
- Upload interrupted: check free disk space and the incoming folder; retry the upload.
- Server logs: the console window shows Python errors.
- External drive: verify `EVERVAULT_DATA_DIR` and confirm the drive is mounted before starting the server.


Configured storage for this setup: `G:\EvervaultData`. Run `migrate_to_g_drive.bat` once to copy an existing C: library safely.
