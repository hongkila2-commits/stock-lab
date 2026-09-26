@echo off
rem ==== One-time KakaoTalk login for alerts (enter KAKAO_REST_API_KEY in .env first) ====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python -m stocklab kakao-login
pause
