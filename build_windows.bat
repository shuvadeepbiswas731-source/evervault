@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --name Evervault --onedir --add-data "app\web;app\web" run_server.py
echo.
echo Build complete. Executable: dist\Evervault\Evervault.exe
echo Note: keep the _internal folder beside the EXE. Media is stored in %%USERPROFILE%%\EvervaultData by default.
pause
