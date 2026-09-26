@echo off
rem ==== StockLab first-time setup (run once) ====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

rem --- 1. Create virtual environment (prefer Python 3.12) ---
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv >nul 2>nul
)
if not exist .venv\Scripts\python.exe (
  where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
)
if not exist .venv\Scripts\python.exe (
  echo [ERROR] Python not found. Install Python 3.12 from python.org
  echo         and check "Add python.exe to PATH" during install.
  pause
  exit /b 1
)

rem --- 2. Required packages ---
echo.
echo [1/3] Installing required packages...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Package install failed. See the first red error line above.
  pause
  exit /b 1
)

rem --- 3. Optional: LightGBM (if blocked, scikit-learn engine is used instead) ---
echo.
echo [2/3] Installing optional LightGBM...
.venv\Scripts\python -m pip install "lightgbm>=4.3"
if errorlevel 1 echo [INFO] LightGBM skipped. The model will use scikit-learn instead.

rem --- 4. Diagnose: which package loads, which is blocked ---
echo.
echo [3/3] Checking installation...
.venv\Scripts\python scripts\doctor.py
if errorlevel 1 (
  echo.
  echo [ERROR] Some required packages failed to load. Send the result above to get help.
  pause
  exit /b 1
)

if not exist .env copy .env.example .env >nul
.venv\Scripts\python -m stocklab env-sync
echo.
echo [OK] Setup complete. Notepad will open .env - enter your KIS keys and save.
notepad .env
pause
