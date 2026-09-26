@echo off
rem ==== Register Windows Task Scheduler: weekdays 18:10 run update.bat ====
chcp 65001 >nul
schtasks /Create /TN "StockLab-Daily" /TR "\"%~dp0update.bat\" auto" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:10 /F
if errorlevel 1 (
  echo [ERROR] Failed. Try right-click - Run as administrator.
) else (
  echo [OK] Registered. The PC must be on at 18:10. Remove with:
  echo      schtasks /Delete /TN "StockLab-Daily" /F
)
pause
