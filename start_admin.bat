@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在以管理员身份启动 Furina Agent...
powershell -NoProfile -Command "Start-Process -FilePath 'python' -ArgumentList 'desktop.py' -WorkingDirectory '%CD%' -Verb RunAs"
