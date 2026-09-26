@echo off
rem ==== Installation check (Smart App Control, blocked packages) ====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python scripts\doctor.py %*
pause
