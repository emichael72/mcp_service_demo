@echo off
setlocal
cls

REM ---------------------------------------------------------------------------
REM run_inspector.cmd
REM
REM Windows helper script to launch the MCP Inspector, a complimentary tool
REM used to test MCP (Model Context Protocol) services.
REM
REM The MCP Inspector provides a simple UI to explore and invoke the methods
REM your MCP service exposes (tools, resources, templates, etc.).
REM
REM Documentation:
REM   https://modelcontextprotocol.io/legacy/tools/inspector
REM
REM Source / Installation:
REM   https://github.com/modelcontextprotocol/inspector
REM
REM Usage:
REM   run_inspector.cmd <IP> [PORT]
REM     <IP>   – the host/IP where your MCP service is listening
REM     [PORT] – the service port (defaults to 6274 if not provided)
REM ---------------------------------------------------------------------------

set MCP_PROXY_AUTH_TOKEN=foobar
set DEFAULT_PORT=6274

REM First argument must exist (IP)
if "%~1"=="" goto :usage

set "IP=%~1"

REM If second arg missing, use default
if "%~2"=="" (
    set "PORT=%DEFAULT_PORT%"
) else (
    set "PORT=%~2"
)

echo Using IP=%IP%, PORT=%PORT%
npx @modelcontextprotocol/inspector --port 7000 http://%IP%:%PORT%
goto :eof

:usage
echo Usage: %~nx0 IP [PORT (%DEFAULT_PORT%)]
exit /b 1
