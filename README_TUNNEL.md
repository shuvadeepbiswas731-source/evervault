# Evervault Tunnel — local prototype patch

This patch adds an app-managed outbound WebSocket tunnel between the Windows Evervault client and the Render FastAPI service. It avoids Tailscale and does not expose inbound ports on the Windows PC. The existing local storage agent remains bound to `127.0.0.1:8766`, and it continues to own all G: reads/writes and file verification.

**This is a local development patch. Do not deploy it to Render or expose the tunnel endpoint until the local checks pass and the Render environment is configured.**

## Files in this patch

- `app/server.py` — based on the previously tested safe remote-access patch. Adds the authenticated WebSocket endpoint and routes remote GET/PUT storage operations through the tunnel when `EVERVAULT_TUNNEL_ENABLED=1`. Website and agent deletion remain disabled.
- `app/storage_agent.py` — retains authenticated GET/PUT and disabled DELETE.
- `app/tunnel.py` — Render-side WebSocket broker, serialized streaming protocol, auth validation and upload/download relay.
- `app/tunnel_client.py` — Windows-side reconnecting client. Connects outbound over WSS and delegates storage operations to `http://127.0.0.1:8766`.
- `requirements.txt` — dependencies including `websockets` for the Windows client.
- `start_tunnel_client.bat` — starts the client manually after the updated Render server is deployed and configured.
- `safety_patch_tests/` and `tunnel_tests/` — local-only regression/protocol tests.

## Protocol outline

1. The Windows client connects to `wss://evervault-94v1.onrender.com/internal/tunnel/ws`.
2. It authenticates with the existing storage-agent Bearer token, read locally from `D:\EvervaultState\storage_agent.token`. The token is never placed in a URL.
3. Render sends a `GET` or `PUT` request control frame. The tunnel uses bounded 256 KiB binary frames for payload transfer and serializes one file operation at a time.
4. The Windows client passes operations to the loopback-only local storage agent. The local agent validates stored names, verifies upload size/SHA-256, and writes only to the configured media root on G:.
5. If the client is disconnected, remote photo reads return unavailable; an upload can still use the existing Google Drive fallback.

## Important operational caveat

Render's free web service can spin down after 15 minutes without inbound HTTP/WebSocket messages. This client sends an application heartbeat every 5 minutes when connected, so a continuously connected tunnel can keep the service active and consume its monthly free instance-hours. Free Render service limits may make reliable 24/7 remote availability impractical; check current usage and plan limits before enabling a permanent connection. See https://render.com/docs/free and https://render.com/docs/websocket.

The current protocol targets a **single Render instance and one Windows client**. Do not scale the Render service horizontally with this in-memory broker. A future multi-device/product rollout needs a shared broker or routing layer and stronger device enrollment/rotation lifecycle.

## Installation on D:

1. Keep the current Render deployment unchanged. Do not push/deploy this patch yet.
2. In Command Prompt, make a fresh backup folder name under `D:\EvervaultState` and back up the current files, for example:

   ```cmd
   cd /d D:\evervault_media_cloud
   mkdir D:\EvervaultState\before_tunnel_patch_20261027
   copy /y app\server.py D:\EvervaultState\before_tunnel_patch_20261027\server.py
   copy /y app\storage_agent.py D:\EvervaultState\before_tunnel_patch_20261027\storage_agent.py
   copy /y requirements.txt D:\EvervaultState\before_tunnel_patch_20261027\requirements.txt
   ```

   If that backup folder already exists, choose a different new name.
3. Extract the ZIP contents into `D:\evervault_media_cloud`, replacing the two listed app files and `requirements.txt`, and adding `app\tunnel.py`, `app\tunnel_client.py`, `start_tunnel_client.bat`, and the test folders. Do not replace `.env` or anything in `credentials`.
4. Install dependencies only into the D: project environment, with temp/cache redirected to D:

   ```cmd
   set "TEMP=D:\EvervaultState\tmp"
   set "TMP=D:\EvervaultState\tmp"
   set "PIP_CACHE_DIR=D:\EvervaultState\pip-cache"
   .venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements-test.txt
   ```
5. With the prompt in `D:\evervault_media_cloud`, run the syntax checks:

   ```cmd
   set "PYTHONDONTWRITEBYTECODE=1"
   .venv\Scripts\python.exe -c "from pathlib import Path; files=['app/server.py','app/storage_agent.py','app/tunnel.py','app/tunnel_client.py']; [compile(Path(f).read_text(encoding='utf-8'),f,'exec') for f in files]; print('Syntax checks passed.')"
   ```
6. Run the tunnel protocol tests:

   ```cmd
   .venv\Scripts\python.exe -m unittest discover -s tunnel_tests -v
   ```
7. Run the existing safety tests:

   ```cmd
   .venv\Scripts\python.exe -m unittest discover -s safety_patch_tests -v
   ```

The tests use synthetic bytes and fake authentication. They must not access your real media, OAuth secrets, `.env`, Neon database, or Google Drive.

## After local tests — later step, do not do yet

Only after reviewing the local output should we prepare the Render deployment. Render will need `EVERVAULT_TUNNEL_ENABLED=1` and `LOCAL_STORAGE_TOKEN` equal to the token file. In tunnel mode, unset `LOCAL_STORAGE_URL` so the app doesn't use a directly exposed local-agent URL. Deploy the code before launching `start_tunnel_client.bat`; before that deployment, the new WebSocket route doesn't exist on Render and the client will simply fail to connect/retry.

No existing photo files are moved/deleted by this patch. Destructive endpoints remain disabled.

`requirements-test.txt` installs the runtime dependencies and the test-only `httpx2` package used by the existing Starlette/FastAPI `TestClient` safety suite. `httpx2` is not needed by the running website itself.
