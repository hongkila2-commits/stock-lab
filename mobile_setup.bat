@echo off
rem ==== Allow phones to reach the dashboard (port 8501) through Windows Firewall ====
rem Run once: right-click - Run as administrator.
rem   Rule 1: home/office (Private) networks, e.g. same Wi-Fi
rem   Rule 2: Tailscale addresses only (100.64.0.0/10), any network profile
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Please right-click mobile_setup.bat and choose "Run as administrator".
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="StockLab Dashboard (LAN)" >nul 2>&1
netsh advfirewall firewall delete rule name="StockLab Dashboard (Tailscale)" >nul 2>&1
netsh advfirewall firewall add rule name="StockLab Dashboard (LAN)" dir=in action=allow protocol=TCP localport=8501 profile=private,domain
netsh advfirewall firewall add rule name="StockLab Dashboard (Tailscale)" dir=in action=allow protocol=TCP localport=8501 remoteip=100.64.0.0/10 profile=any
if errorlevel 1 (
  echo [ERROR] Failed to add firewall rules.
) else (
  echo [OK] Firewall allows port 8501. Open the dashboard sidebar - "Phone" section for the address / QR code.
  echo      Remove later with:
  echo      netsh advfirewall firewall delete rule name="StockLab Dashboard (LAN)"
  echo      netsh advfirewall firewall delete rule name="StockLab Dashboard (Tailscale)"
)
pause
