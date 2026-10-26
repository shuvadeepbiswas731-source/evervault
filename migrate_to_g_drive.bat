@echo off
setlocal
title Evervault - Move library to G drive
echo ==========================================================
echo   EVERVAULT STORAGE MIGRATION
echo ==========================================================
echo.
echo IMPORTANT: Close the Evervault server window before continuing.
echo This copies your existing library from C: to G: and keeps
echo the original C: copy as a safety backup.
echo.
if not exist G:\ (
  echo ERROR: Drive G: is not available. Connect it first.
  pause
  exit /b 1
)
set "SOURCE=%USERPROFILE%\EvervaultData"
set "DEST=G:\EvervaultData"
if not exist "%SOURCE%\evervault.sqlite3" (
  echo No existing Evervault database found at:
  echo %SOURCE%
  echo.
  echo If you have not uploaded anything yet, this is fine.
  if not exist "%DEST%" mkdir "%DEST%"
  goto :configure
)
if not exist "%DEST%" mkdir "%DEST%"
echo Source:      %SOURCE%
echo Destination: %DEST%
echo.
robocopy "%SOURCE%" "%DEST%" /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 /XJ
if %ERRORLEVEL% GEQ 8 (
  echo.
  echo ERROR: Copy failed. Your original C: library has not been deleted.
  pause
  exit /b 2
)
echo.
echo Copy finished. Original C: data has been preserved.
:configure
setx EVERVAULT_DATA_DIR "G:\EvervaultData" >nul
echo.
echo Configured EVERVAULT_DATA_DIR=G:\EvervaultData for future terminals.
echo The launcher also explicitly uses G:\EvervaultData.
echo.
echo Next: run start_windows.bat. Check that your existing photos appear.
echo Do NOT delete the old C: EvervaultData folder until you have verified
echo the gallery and checked that your photos play correctly.
echo.
pause
