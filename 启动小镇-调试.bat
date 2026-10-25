@echo off
REM ============================================================
REM  像素小镇 - debug launcher (keeps the console open)
REM
REM  Use this when the normal launcher seems to do nothing:
REM  any Python traceback stays on screen instead of vanishing.
REM
REM  Set PIXELTOWN_NOPAUSE=1 to run unattended (self-tests).
REM  This file must keep CRLF line endings.
REM ============================================================
setlocal
cd /d "%~dp0"

set PY=
if exist "python_path.txt" set /p PY=<python_path.txt
if "%PY%"=="" set PY=python

echo Using interpreter: %PY%
echo Working dir: %CD%
echo.

"%PY%" -m town.app

echo.
echo --- town exited with code %ERRORLEVEL% ---
if not "%PIXELTOWN_NOPAUSE%"=="1" pause
exit /b 0
