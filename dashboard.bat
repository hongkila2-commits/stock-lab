@echo off
rem ==== Open dashboard at http://localhost:8501 (close this window to stop) ====
rem Listens on all network addresses so a phone (same Wi-Fi or Tailscale) can connect too.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
rem Stop a dashboard that is still running (old code would keep serving the browser)
.venv\Scripts\python -m stocklab stop-dashboard
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
.venv\Scripts\python -m streamlit run app\dashboard.py --server.address 0.0.0.0
