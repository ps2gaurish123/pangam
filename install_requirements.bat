@echo off
setlocal

REM Install project requirements into a local virtual environment.
set "VENV_DIR=.venv"

where py >nul 2>nul
if %errorlevel% neq 0 (
  echo [ERROR] Python launcher (py) not found. Please install Python 3.10+ and retry.
  exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo [INFO] Creating virtual environment in %VENV_DIR%
  py -3 -m venv "%VENV_DIR%"
  if %errorlevel% neq 0 (
    echo [ERROR] Failed to create virtual environment.
    exit /b 1
  )
)

echo [INFO] Upgrading pip...
call "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if %errorlevel% neq 0 (
  echo [ERROR] Failed to upgrade pip.
  exit /b 1
)

if exist requirements.txt (
  echo [INFO] Installing requirements from requirements.txt...
  call "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
  if %errorlevel% neq 0 (
    echo [ERROR] Failed to install requirements.
    exit /b 1
  )
) else (
  echo [WARN] requirements.txt not found. Skipping dependency installation.
)

echo [OK] Environment is ready.
exit /b 0
