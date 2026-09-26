@echo off
rem ==== Register Windows Task Scheduler ====
rem   StockLab-Daily    : weekdays 18:10 update.bat (collect, predict, evening KakaoTalk summary)
rem   StockLab-Realtime : weekdays 08:55 realtime.bat (intraday quotes until 15:35)
chcp 65001 >nul
schtasks /Create /TN "StockLab-Daily" /TR "\"%~dp0update.bat\" auto" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:10 /F
if errorlevel 1 goto fail
schtasks /Create /TN "StockLab-Realtime" /TR "\"%~dp0realtime.bat\" auto" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:55 /F
if errorlevel 1 goto fail
echo [OK] Registered. The PC must be on at 08:55 and 18:10. Remove with:
echo      schtasks /Delete /TN "StockLab-Daily" /F
echo      schtasks /Delete /TN "StockLab-Realtime" /F
pause
exit /b 0
:fail
echo [ERROR] Failed. Try right-click - Run as administrator.
pause
exit /b 1
