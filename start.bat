@echo off
title CS2 ValuEyes
cd /d "%~dp0"

echo ============================================
echo     CS2 ValuEyes v3.0
echo ============================================
echo.

:: ---------- find Python (try py launcher first) ----------
set PYTHON=
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py
) else (
    python --version >nul 2>&1
    if %errorlevel% equ 0 (
        set PYTHON=python
    ) else (
        echo [ERROR] Python not found! Please install Python 3.10+
        echo         https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
echo [OK] Using: %PYTHON%
%PYTHON% --version

:: ---------- check data file ----------
dir *id*.json >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Missing data file
    pause
    exit /b 1
)
echo [OK] Data file found

:: ---------- csqaq_api.py ----------
if not exist "csqaq_api.py" (
    echo [ERROR] Missing csqaq_api.py
    pause
    exit /b 1
)

:: ---------- venv ----------
if not exist ".venv" (
    echo [..] Creating virtual environment...
    %PYTHON% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)

echo [..] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [WARN] Some deps failed, trying to start anyway...
)

:: ---------- find free port ----------
set PORT=8000
:port_loop
netstat -ano 2>nul | findstr /C:":%PORT% " >nul 2>&1
if %errorlevel% equ 0 (
    set /a PORT+=1
    if %PORT% gtr 8100 (
        echo [ERROR] Ports 8000-8100 all in use
        pause
        exit /b 1
    )
    goto port_loop
)
echo [OK] Port %PORT% is available

:: ---------- launch ----------
echo.
echo ============================================
echo     Starting server...
echo.
echo     Visit: http://localhost:%PORT%/ui
echo     Press Ctrl+C to stop
echo ============================================
echo.
start http://localhost:%PORT%/ui
%PYTHON% csqaq_api.py --port %PORT%
echo.
echo [INFO] Server stopped (press any key to close)
pause
