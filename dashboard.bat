@echo off
rem ==== Open dashboard at http://localhost:8501 (close this window to stop) ====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
.venv\Scripts\python -m streamlit run app\dashboard.py
