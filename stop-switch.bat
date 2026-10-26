@echo off
rem Clash 自动切换器 - 停止
taskkill /IM ClashAutoSwitch.exe /F 2>nul
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq *" /F 2>nul | findstr /I "clash" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'clash_auto_switch' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo 已停止 ClashAutoSwitch。
pause
