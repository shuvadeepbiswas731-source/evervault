@echo off
set /p DRIVEPATH=Enter your media storage folder, e.g. E:\EvervaultData:
if not exist "%DRIVEPATH%" mkdir "%DRIVEPATH%"
echo.
echo To use this folder, set EVERVAULT_DATA_DIR to:
echo %DRIVEPATH%
echo.
echo For a permanent setting, open PowerShell and run:
echo setx EVERVAULT_DATA_DIR "%DRIVEPATH%"
echo Then close and reopen the terminal before starting Evervault.
pause
