@echo off
rem ==== Collect data, screen, predict. Run daily after market close. ====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python -m stocklab update
if not "%1"=="auto" pause
