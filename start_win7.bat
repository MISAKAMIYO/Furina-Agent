@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM ============================================================
REM  Furina Agent - Windows 7 / Python 3.8.10 启动器
REM  只启动 Agent 后端（设置 / 会话 / 流式对话 / 工具 / 记忆）
REM  界面用系统默认浏览器打开
REM ============================================================

set "PY=%~dp0.venv38\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [win7] using %PY%
"%PY%" -c "import sys; print('[win7] python', sys.version.split()[0])"

REM 可选：固定端口；不设则自动挑空闲端口
REM set ZHIYAN_PORT=8001

"%PY%" "%~dp0win7\launch.py" %*
if errorlevel 1 (
    echo.
    echo [win7] 启动失败。请确认：
    echo   1^) 已安装 Python 3.8.10（勾选 Add python.exe to PATH）
    echo   2^) 已执行 python -m pip install -r win7\requirements-win7.txt
    pause
)
endlocal
