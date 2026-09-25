@echo off
setlocal
set "ROOT_DIR=%~dp0"
set "VENV_PYTHON=%ROOT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo [ai-company-manager] Virtual environment is missing. Run: python install.py 1>&2
  exit /b 1
)

if "%COMPANY_DATA_DIR%"=="" set "COMPANY_DATA_DIR=%ROOT_DIR%company_data"
if "%COMPANY_MAX_FILE_MB%"=="" set "COMPANY_MAX_FILE_MB=10"

"%VENV_PYTHON%" "%ROOT_DIR%src\server.py"
