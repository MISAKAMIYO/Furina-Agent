@echo off
chcp 65001 >nul
cd /d "%~dp0"
title FURINA AGENT WEB - Build

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

python "%~dp0tools\build.py" %*
set "RC=%errorlevel%"

if not "%RC%"=="0" echo [ERROR] build failed, exit code %RC%
if "%~1"=="" pause
exit /b %RC%
