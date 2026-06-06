@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" goto MENU
if /I "%MODE%"=="llm" goto START
if /I "%MODE%"=="--llm" (
    set "MODE=llm"
    goto START
)
if /I "%MODE%"=="no-llm" goto START
if /I "%MODE%"=="no_llm" (
    set "MODE=no-llm"
    goto START
)
if /I "%MODE%"=="nollm" (
    set "MODE=no-llm"
    goto START
)
if /I "%MODE%"=="--no-llm" (
    set "MODE=no-llm"
    goto START
)
if /I "%MODE%"=="help" goto HELP
if /I "%MODE%"=="--help" goto HELP
echo [ERROR] Unknown mode: %MODE%
goto BAD_USAGE

:MENU
echo ========================================
echo Agent-A2A local services
echo ========================================
echo 1. Use LLM
echo 2. No LLM
echo.
choice /C 12 /N /M "Select mode [1=LLM, 2=No LLM]: "
if errorlevel 2 (
    set "MODE=no-llm"
) else (
    set "MODE=llm"
)
goto START

:START
where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv is not found. Please install uv first.
    pause
    exit /b 1
)

if /I "%MODE%"=="llm" (
    set "A2A_USE_LLM=1"
    set "A2A_LLM_ENABLED=1"
    set "A2A_DEMO_FAST=0"
) else (
    set "MODE=no-llm"
    set "A2A_USE_LLM=0"
    set "A2A_LLM_ENABLED=0"
    set "A2A_DEMO_FAST=1"
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

echo.
echo Starting all local A2A/MCP services in "%MODE%" mode...
echo Project directory: %CD%
echo.
uv run python scripts\start_all.py --mode %MODE%
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo start_all exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:HELP
echo Usage:
echo   start_all.bat             choose mode interactively
echo   start_all.bat llm         start with external LLM calls
echo   start_all.bat no-llm      start without external LLM calls
exit /b 0

:BAD_USAGE
echo Usage:
echo   start_all.bat             choose mode interactively
echo   start_all.bat llm         start with external LLM calls
echo   start_all.bat no-llm      start without external LLM calls
exit /b 1
