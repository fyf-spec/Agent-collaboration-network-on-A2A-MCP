@echo off
setlocal

cd /d "%~dp0"

echo ========================================
echo Starting Agent-A2A local demo services...
echo Project directory: %CD%
echo ========================================

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv is not found. Please install uv first.
    pause
    exit /b 1
)

echo.
echo [1/5] Starting Weather MCP Server on 127.0.0.1:8001...
start "Weather MCP Server :8001" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%CD%'; uv run python mcp_servers/weather_mcp_server.py"

timeout /t 1 /nobreak >nul

echo [2/5] Starting Traffic MCP Server on 127.0.0.1:8002...
start "Traffic MCP Server :8002" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%CD%'; uv run python mcp_servers/traffic_mcp_server.py"

timeout /t 1 /nobreak >nul

echo [3/5] Starting Weather Agent on 127.0.0.1:9010...
start "Weather Agent :9010" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%CD%'; uv run python agents/weather_agent.py"

timeout /t 1 /nobreak >nul

echo [4/5] Starting Traffic Agent on 127.0.0.1:9020...
start "Traffic Agent :9020" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%CD%'; uv run python agents/traffic_agent.py"

timeout /t 1 /nobreak >nul

echo [5/5] Starting Coordinator on 127.0.0.1:9000...
start "Coordinator :9000" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%CD%'; uv run python coordinator.py"

echo.
echo ========================================
echo All services are starting.
echo.
echo Please check the opened PowerShell windows.
echo Expected services:
echo   Weather MCP Server   http://127.0.0.1:8001
echo   Traffic MCP Server   http://127.0.0.1:8002
echo   Weather Agent        http://127.0.0.1:9010
echo   Traffic Agent        http://127.0.0.1:9020
echo   Coordinator          http://127.0.0.1:9000
echo ========================================
echo.
pause