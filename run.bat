@echo off
REM Quick Launcher for Video-to-Transcript Data Pipeline
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at .venv\Scripts\python.exe.
    echo Please install dependencies first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" run_pipeline.py %*

endlocal
