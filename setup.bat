@echo off
rem ==== StockLab first-time setup (run once) ====
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
)
if not exist .venv\Scripts\python.exe (
  echo [ERROR] Python not found. Install Python 3.11 or 3.12 from python.org
  echo         and check "Add python.exe to PATH" during install.
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Package install failed. See messages above.
  pause
  exit /b 1
)
if not exist .env copy .env.example .env >nul
echo.
echo [OK] Setup complete. Notepad will open .env - enter your KIS keys and save.
notepad .env
pause
