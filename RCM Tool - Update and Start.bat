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

echo Applying RC filter wiring if needed...
python apply_rc_wiring.py
if errorlevel 1 (
    echo RC wiring failed.
    pause
    exit /b 1
)

echo Applying UI and capture-performance pass if needed...
python apply_ui_pass.py
if errorlevel 1 (
    echo UI pass failed. Run apply_rc_wiring.py first, then apply_ui_pass.py.
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
