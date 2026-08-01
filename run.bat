@echo off
rem sdl launcher: creates a local venv on first run, then starts the TUI.
setlocal
cd /d "%~dp0"

rem UTF-8 console: language labels and the progress bars are not ASCII.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY=%~dp0.venv\Scripts\python.exe"
set "STAMP=%~dp0.venv\.installed"

if not exist "%PY%" (
    echo [sdl] Primo avvio: creazione dell'ambiente virtuale...
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv ".venv"
    ) else (
        py -3 -m venv ".venv"
    )
)

if not exist "%PY%" (
    echo.
    echo [sdl] Python 3 non trovato. Installalo da https://www.python.org/downloads/
    echo        ricordando di spuntare "Add python.exe to PATH", poi riavvia questo file.
    echo.
    pause
    exit /b 1
)

if not exist "%STAMP%" (
    echo [sdl] Installazione delle dipendenze...
    "%PY%" -m pip install --upgrade pip --quiet
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [sdl] Installazione delle dipendenze fallita.
        pause
        exit /b 1
    )
    echo ok> "%STAMP%"
)

"%PY%" -m sdl %*
if errorlevel 1 pause
endlocal
