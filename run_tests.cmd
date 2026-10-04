@echo off
rem MCP_AI automated test entry point.
rem   run_tests.cmd                         run everything
rem   run_tests.cmd -m "not integration"    fast subset (unit + component only)
rem   run_tests.cmd tests/test_extract.py -v
rem NOTE: keep this file ASCII-only; cmd.exe reads it in the OEM code page and
rem       non-ASCII bytes get mangled into bogus commands.
setlocal
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m pytest %*
