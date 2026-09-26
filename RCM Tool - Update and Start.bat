@echo off
setlocal
cd /d "%~dp0"

rem Local changes never stop the update. Git itself refuses to overwrite
rem uncommitted work, so pull, and if git declines, start with the local code.
set "RCM_LOCAL_CHANGES="
for /f "delims=" %%S in ('git status --porcelain') do set "RCM_LOCAL_CHANGES=1"
if defined RCM_LOCAL_CHANGES (
    echo Note: this folder has changes that are not on GitHub yet. They are kept; nothing is waiting.
    git status --short
    echo.
)

echo Updating RCM Tool from GitHub...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo The update could not be applied on top of the local changes, so nothing was changed.
    echo RCM Tool will start with the code already in this folder.
    echo.
)

echo Checking required controller backends...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

rem Start RcmTool as its own program (pythonw has no console window), so
rem closing this launcher can never close the app.
echo Starting RCM Tool. Its window appears in a few seconds.
where pythonw >nul 2>nul
if errorlevel 1 (
    start "RCM Tool" python rcm_tool.py
) else (
    start "" pythonw rcm_tool.py
)
timeout /t 4 /nobreak >nul
exit /b 0
