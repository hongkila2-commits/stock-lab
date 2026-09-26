@echo off
rem ==== Demo with synthetic data (no API keys needed). Real data is untouched. ====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set STOCKLAB_DB=data\demo.sqlite
.venv\Scripts\python -m stocklab demo --if-empty
if errorlevel 1 (
  echo [ERROR] Demo data generation failed. See messages above.
  pause
  exit /b 1
)
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
.venv\Scripts\python -m streamlit run app\dashboard.py --server.address 0.0.0.0
