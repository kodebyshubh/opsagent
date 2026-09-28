@echo off
REM One-click Windows setup: creates .venv inside the project, installs OpsAgent,
REM seeds the demo data and (optionally) generates the sample transcripts.
cd /d "%~dp0\.."
echo === Python version (3.11 to 3.13 expected):
python --version || (echo Python not found on PATH & pause & exit /b 1)
if not exist .venv (
  echo === Creating .venv
  python -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
echo === Installing OpsAgent (first time takes a few minutes)
python -m pip install --upgrade pip >nul
python -m pip install -e ".[ui,dev]" || (echo Install failed & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo === Seeding demo data
opsagent seed
if /I "%1"=="notranscripts" goto done
echo === Generating transcripts with the live model (about 5 minutes)
python scripts\make_transcripts.py
:done
echo.
echo === Done. To use it later: open PowerShell here and run  .venv\Scripts\Activate.ps1
pause
