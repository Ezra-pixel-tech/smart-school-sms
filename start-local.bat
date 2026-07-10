@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)
if not exist ".env" (
  copy ".env.example" ".env" >nul
  powershell -NoProfile -Command "(Get-Content '.env') -replace '^DATABASE_URL=.*','DATABASE_URL=sqlite:///smart_schools_sms.db' -replace '^SESSION_COOKIE_SECURE=.*','SESSION_COOKIE_SECURE=0' | Set-Content '.env'"
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
