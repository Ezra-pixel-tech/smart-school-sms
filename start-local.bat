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
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$bytes=New-Object byte[] 48; $rng=[Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($bytes); $rng.Dispose(); $secret=[Convert]::ToBase64String($bytes); (Get-Content '.env') -replace '^SECRET_KEY=.*',('SECRET_KEY='+$secret) -replace '^FLASK_DEBUG=.*','FLASK_DEBUG=1' -replace '^DATABASE_URL=.*','DATABASE_URL=sqlite:///smart_schools_sms.db' -replace '^SESSION_COOKIE_SECURE=.*','SESSION_COOKIE_SECURE=0' -replace '^BOOTSTRAP_ADMIN_PASSWORD=.*','BOOTSTRAP_ADMIN_PASSWORD=Admin@12345' | Set-Content '.env'"
  echo.
  echo First local login: admin / Admin@12345
  echo You will be required to change this temporary password.
)

if not exist "uploads" mkdir uploads

echo Installing required packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check your internet connection, then run this file again.
  pause
  exit /b 1
)

echo Server running at http://127.0.0.1:5000
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:5000'"
".venv\Scripts\python.exe" run.py

pause
