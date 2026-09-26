@echo off
rem ==== Intraday real-time quotes (weekdays 09:00-15:30). Close this window to stop. ====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python -m stocklab realtime
if not "%1"=="auto" pause
