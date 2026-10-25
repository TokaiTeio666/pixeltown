@echo off
REM ============================================================
REM  像素小镇 - stop the running town (graceful first)
REM
REM  1. drop save\stop.flag -> the app saves and closes itself
REM  2. if it is still alive after ~20s, fall back to taskkill
REM
REM  Graceful is the default because a forced kill loses whatever
REM  happened since the last autosave, and taskkill is often blocked
REM  by antivirus or permissions.
REM
REM  Uses save\app.pid written by the app, so it never touches other
REM  Python programs. PIXELTOWN_NOPAUSE=1 runs it unattended.
REM
REM  This file must keep CRLF line endings - cmd.exe mis-parses
REM  goto labels in LF-only batch files.
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist "save\app.pid" (
  echo No save\app.pid found - the town does not look like it is running.
  echo You can also right-click the island and pick the exit item.
  if not "%PIXELTOWN_NOPAUSE%"=="1" pause
  exit /b 0
)

if not exist "save" mkdir "save"
type nul > "save\stop.flag"
echo Asked the town to close, it will save first...

set /a TRIES=0

:waitloop
if not exist "save\app.pid" goto stopped
set /a TRIES+=1
if %TRIES% GEQ 20 goto force
ping -n 2 127.0.0.1 >nul
goto waitloop

:stopped
echo Stopped gracefully. Progress saved.
goto done

:force
echo Graceful stop timed out - forcing.
set /p PID=<save\app.pid
taskkill /PID %PID% /F
if errorlevel 1 (
  echo taskkill failed - try Task Manager.
) else (
  echo Forced stop done.
)
del "save\app.pid" 2>nul
del "save\stop.flag" 2>nul

:done
if not "%PIXELTOWN_NOPAUSE%"=="1" pause
exit /b 0
