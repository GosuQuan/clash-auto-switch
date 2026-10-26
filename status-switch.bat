@echo off
rem Clash 自动切换器 - 查看状态
chcp 65001 >nul
set PY=C:\Users\admin\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
"%PY%" "%~dp0clash_auto_switch.py" --status
echo.
pause
