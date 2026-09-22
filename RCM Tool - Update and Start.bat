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

echo Applying prototype wiring...
python apply_all.py
if errorlevel 1 (
    echo Wiring failed. If an apply script could not find a block:
    echo   git checkout -- controller_integrity.py
    echo   python apply_all.py
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
