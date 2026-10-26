@echo off
rem Clash 自动切换器 - 启动（exe 版，隐藏窗口常驻，重复启动会自动退出）
start "" "%~dp0ClashAutoSwitch.exe"
echo 已启动。看板地址: http://127.0.0.1:38766
echo 日志: %~dp0auto-switch.log
timeout /t 3 >nul
