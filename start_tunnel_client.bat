@echo off
setlocal
cd /d "D:\evervault_media_cloud"

rem Evervault Tunnel client settings. The tunnel client initiates outbound WSS;
rem it does not open a public port on this PC.
set "EVERVAULT_STATE_DIR=D:\EvervaultState"
set "EVERVAULT_AGENT_TOKEN_FILE=D:\EvervaultState\storage_agent.token"
set "EVERVAULT_LOCAL_AGENT_URL=http://127.0.0.1:8766"
set "EVERVAULT_TUNNEL_STAGING_DIR=D:\EvervaultState\tunnel-staging"
set "EVERVAULT_TUNNEL_URL=wss://evervault-94v1.onrender.com/internal/tunnel/ws"
set "TEMP=D:\EvervaultState\tmp"
set "TMP=D:\EvervaultState\tmp"
set "PIP_CACHE_DIR=D:\EvervaultState\pip-cache"

if not exist "D:\EvervaultState\storage_agent.token" (
  echo ERROR: Storage-agent token file is missing.
  echo Expected: D:\EvervaultState\storage_agent.token
  pause
  exit /b 1
)

if not exist "D:\EvervaultState\tunnel-staging" mkdir "D:\EvervaultState\tunnel-staging"

if not exist "D:\evervault_media_cloud\.venv\Scripts\python.exe" (
  echo ERROR: Project virtual environment is missing on D:.
  pause
  exit /b 1
)

"D:\evervault_media_cloud\.venv\Scripts\python.exe" -m app.tunnel_client
pause
