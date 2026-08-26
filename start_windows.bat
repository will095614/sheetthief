@echo off
setlocal
rem Piano Score Extractor - Windows one-click launcher
rem (All messages here are kept in plain English on purpose: Windows' cmd.exe
rem  reads .bat files using the system's legacy codepage, not UTF-8, so Chinese
rem  text in a .bat file can get corrupted and break command parsing on some
rem  Windows locales. See README.md for the Chinese explanation of each step.)

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on this computer.
  echo Please install Python 3.9+ from https://www.python.org/downloads/
  echo During install, make sure to check "Add Python to PATH", then run this file again.
  pause
  exit /b 1
)

if not exist venv (
  echo First run: creating a virtual environment...
  python -m venv venv
)

call venv\Scripts\activate.bat

if not exist venv\.deps_installed (
  echo Installing required packages, this can take a few minutes the first time...
  python -m pip install --upgrade pip --quiet --no-cache-dir
  pip install -r requirements.txt --quiet --no-cache-dir
  type nul > venv\.deps_installed
)

echo.
echo Starting server...
start "" /B cmd /c "timeout /t 2 >nul & start http://127.0.0.1:5002"

python app.py

pause
