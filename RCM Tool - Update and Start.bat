@echo off
setlocal
cd /d "%~dp0"

if not exist "%LOCALAPPDATA%\RCMTool\logs" mkdir "%LOCALAPPDATA%\RCMTool\logs"
if not exist "%LOCALAPPDATA%\RCMTool\backups" mkdir "%LOCALAPPDATA%\RCMTool\backups"
set "RCM_LAUNCH_LOG=%LOCALAPPDATA%\RCMTool\logs\launch.log"
set "RCM_BACKUP_DIR=%LOCALAPPDATA%\RCMTool\backups"

rem apply_all.py intentionally patches controller_integrity.py for runtime use.
rem If a previous run was interrupted before cleanup, preserve that local copy,
rem restore the tracked source, and then update safely.
set "RCM_CONTROLLER_DIRTY="
for /f "delims=" %%S in ('git status --porcelain -- controller_integrity.py') do set "RCM_CONTROLLER_DIRTY=1"
if defined RCM_CONTROLLER_DIRTY (
    for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RCM_TS=%%T"
    copy /Y "controller_integrity.py" "%RCM_BACKUP_DIR%\controller_integrity-local-%RCM_TS%.py" >nul
    git diff -- controller_integrity.py > "%RCM_BACKUP_DIR%\controller_integrity-local-%RCM_TS%.patch"
    git diff --cached -- controller_integrity.py >> "%RCM_BACKUP_DIR%\controller_integrity-local-%RCM_TS%.patch"
    echo Preserved local controller_integrity.py changes in:
    echo   %RCM_BACKUP_DIR%
    git restore --source=HEAD --staged --worktree -- controller_integrity.py
    if errorlevel 1 (
        echo.
        echo Could not restore controller_integrity.py safely.
        pause
        exit /b 1
    )
)

rem Do not overwrite unrelated local work. Show it and stop for review.
set "RCM_OTHER_DIRTY="
for /f "delims=" %%S in ('git status --porcelain') do set "RCM_OTHER_DIRTY=1"
if defined RCM_OTHER_DIRTY (
    echo.
    echo Update stopped because other local changes are present:
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

echo Applying prototype wiring...
python apply_all.py
if errorlevel 1 (
    git restore --source=HEAD --staged --worktree -- controller_integrity.py >nul 2>&1
    echo Wiring failed. The tracked controller source was restored.
    pause
    exit /b 1
)

echo Installing or updating required controller backends...
python -m pip install -r requirements.txt
if errorlevel 1 (
    git restore --source=HEAD --staged --worktree -- controller_integrity.py >nul 2>&1
    echo.
    echo Dependency installation failed. Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

echo Starting RCM Tool...
python rcm_tool.py
set "RCM_EXITCODE=%ERRORLEVEL%"

rem Keep the working tree clean after the app closes so the next pull cannot
rem collide with transient apply_all.py changes.
git restore --source=HEAD --staged --worktree -- controller_integrity.py >nul 2>&1

echo EXITCODE=%RCM_EXITCODE%
pause
exit /b %RCM_EXITCODE%
