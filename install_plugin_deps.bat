@echo off
cd /d "%~dp0"
title Install Furina plugin deps
python install_plugin_deps.py
echo.
echo Exit code: %ERRORLEVEL%
pause