@echo off
chcp 65001 >nul 2>&1
title Furina Agent - Dependencies Installer
cd /d "%~dp0"

echo ============================================
echo   Furina Agent Web - Dependency Installer
echo ============================================
echo.

:: Step 1: Check Python
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+
    echo         Windows 7 users: install Python 3.8.10 instead
    echo         Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set pyver=%%i
echo   Python %pyver%
echo.

:: Step 2: Check pip
echo [2/5] Checking pip...
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] pip not found.
    pause
    exit /b 1
)
echo   pip OK
echo.

:: Step 3: Upgrade pip
echo [3/5] Upgrading pip...
python -m pip install --upgrade pip -q
echo   Done
echo.

:: Step 4: Install core dependencies
echo [4/5] Installing core dependencies...
echo.
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Batch install failed, trying one by one...
    echo.
    for /f "usebackq delims=" %%i in (`type requirements.txt ^| findstr /v /b "#" ^| findstr /v "^$"`) do (
        echo   Installing: %%i
        python -m pip install "%%i" -q
    )
)
echo.
echo   Core dependencies installed
echo.

:: Step 5: Verify installation
echo [5/5] Verifying dependencies...
echo.

:: Create a temp verification script to avoid cmd parsing issues
set "VERIFY_SCRIPT=%TEMP%\zhiyan_verify_deps.py"
(
echo import sys
echo ok = True
echo for mod in ['fastapi','uvicorn','pydantic','requests','httpx','aiohttp','edge_tts','pyttsx3','webview','mutagen','yaml','Crypto','tiktoken','pyperclip','urllib3']:
echo     try:
echo         __import__(mod)
echo         print('  [OK] ' + mod)
echo     except ImportError:
echo         print('  [!!] ' + mod + ' - MISSING')
echo         ok = False
echo print()
echo if ok:
echo     print('  All dependencies ready!')
echo else:
echo     print('  Some packages are missing, check the list above.')
) > "%VERIFY_SCRIPT%"

python "%VERIFY_SCRIPT%"
del "%VERIFY_SCRIPT%" >nul 2>&1

echo.
echo ============================================
echo   Installation complete!
echo   Start: python desktop.py
echo ============================================
echo.
pause