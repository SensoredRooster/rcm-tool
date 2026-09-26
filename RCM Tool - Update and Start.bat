@echo off
setlocal
cd /d "%~dp0"

rem Preserve local work and stop before updating if source changes are pending.
set "RCM_OTHER_DIRTY="
for /f "delims=" %%S in ('git status --porcelain') do set "RCM_OTHER_DIRTY=1"
if defined RCM_OTHER_DIRTY (
    echo.
    echo Update stopped because local changes are present:
    git status --short
    echo.
    echo Commit, stash, or review those changes, then run this updater again.
    pause
    exit /b 1
)

echo Updating RCM Tool from GitHub...
git pull --ff-only
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
set "RCM_EXITCODE=%ERRORLEVEL%"
echo EXITCODE=%RCM_EXITCODE%
pause
exit /b %RCM_EXITCODE%
