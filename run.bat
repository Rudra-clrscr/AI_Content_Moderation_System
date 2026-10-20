@echo off
REM Quick Launcher for Video-to-Transcript Data Pipeline
setlocal

cd /d "%~dp0"

set "PYTHON_EXE="
if exist "flask_api\.venv\Scripts\python.exe" set "PYTHON_EXE=flask_api\.venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

if "%PYTHON_EXE%"=="" (
    echo [ERROR] Virtual environment not found at flask_api\.venv\Scripts\python.exe or .venv\Scripts\python.exe.
    echo Please install dependencies first.
    pause
    exit /b 1
)

"%PYTHON_EXE%" run_pipeline.py %*

endlocal
