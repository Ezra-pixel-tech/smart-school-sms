@echo off
setlocal
cd /d "%~dp0"

echo Starting Smart Schools SMS...

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv. Please install Python and try again.
    pause
    exit /b 1
  )
)

if not exist ".env" (
  echo Creating local .env file...
  copy ".env.example" ".env" >nul
  powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-Content '.env') -replace '^DATABASE_URL=.*','DATABASE_URL=sqlite:///smart_schools_sms.db' -replace '^SESSION_COOKIE_SECURE=.*','SESSION_COOKIE_SECURE=0' | Set-Content '.env'"
)

if not exist "uploads" mkdir uploads

echo Installing required packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check your internet connection, then run this file again.
  pause
  exit /b 1
)

echo Opening Smart Schools SMS in your browser...
start "" "http://127.0.0.1:5000"

echo Server running at http://127.0.0.1:5000
".venv\Scripts\python.exe" run.py

pause
