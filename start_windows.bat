@echo off
setlocal
cd /d "%~dp0"
if not exist G:\ (
  echo ERROR: Drive G: is not available.
  echo Connect the drive and try again. Your media library is configured for G:\EvervaultData.
  pause
  exit /b 1
)
set "EVERVAULT_DATA_DIR=G:\EvervaultData"
if not exist "%EVERVAULT_DATA_DIR%" mkdir "%EVERVAULT_DATA_DIR%"
echo.
echo  EVERVAULT PERSONAL MEDIA
echo  Storage: %EVERVAULT_DATA_DIR%
echo.
if not exist "%EVERVAULT_DATA_DIR%\evervault.sqlite3" (
  if exist "%USERPROFILE%\EvervaultData\evervault.sqlite3" (
    echo A previous C: drive library was found.
    echo Run migrate_to_g_drive.bat first, then start Evervault again.
    pause
    exit /b 2
  )
)
if not exist .venv (
  py -3 -m venv .venv
  if errorlevel 1 goto :pythonerror
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 goto :installerror
python run_server.py
pause
exit /b 0
:pythonerror
echo Python 3 was not found. Install Python 3.11 or newer and enable "Add Python to PATH".
pause
exit /b 1
:installerror
echo Dependency installation failed. Check your internet connection and try again.
pause
exit /b 1
