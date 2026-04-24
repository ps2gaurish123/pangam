@echo off
setlocal ENABLEDELAYEDEXPANSION

echo ================================================
echo   News Fact-Checking System - Local Runner
echo ================================================
echo 1. Install + run from current folder
echo 2. Download (git clone) then install + run
echo.
set /p CHOICE=Choose option [1/2]: 

if "%CHOICE%"=="1" goto run_here
if "%CHOICE%"=="2" goto clone_and_run

echo [ERROR] Invalid option.
exit /b 1

:run_here
if not exist install_requirements.bat (
  echo [ERROR] install_requirements.bat not found in current folder.
  exit /b 1
)

call install_requirements.bat
if %errorlevel% neq 0 exit /b %errorlevel%

call .venv\Scripts\python.exe -m news_factcheck.demo
exit /b %errorlevel%

:clone_and_run
where git >nul 2>nul
if %errorlevel% neq 0 (
  echo [ERROR] git is not installed or not on PATH.
  exit /b 1
)

set /p REPO_URL=Enter git repository URL: 
set /p TARGET_DIR=Enter local folder name to clone into [news-factcheck-local]: 
if "%TARGET_DIR%"=="" set "TARGET_DIR=news-factcheck-local"

if exist "%TARGET_DIR%" (
  echo [ERROR] Folder "%TARGET_DIR%" already exists. Choose another name.
  exit /b 1
)

git clone "%REPO_URL%" "%TARGET_DIR%"
if %errorlevel% neq 0 (
  echo [ERROR] Clone failed.
  exit /b 1
)

pushd "%TARGET_DIR%"
if not exist install_requirements.bat (
  echo [ERROR] install_requirements.bat not found in cloned repository.
  popd
  exit /b 1
)

call install_requirements.bat
if %errorlevel% neq 0 (
  popd
  exit /b %errorlevel%
)

call .venv\Scripts\python.exe -m news_factcheck.demo
set "RC=%errorlevel%"
popd
exit /b %RC%
