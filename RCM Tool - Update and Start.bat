@echo off
setlocal
cd /d "%~dp0"

echo Updating RCM Tool from GitHub...
git pull --ff-only origin master
if errorlevel 1 (
    echo.
    echo Update failed. Your local folder may contain changes that need review.
    pause
    exit /b 1
)

echo Installing or updating required controller backends...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

echo Starting RCM Tool...
python rcm_tool.py
pause
