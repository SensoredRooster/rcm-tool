@echo off
setlocal
cd /d "%~dp0"

echo Checking required controller backends...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Make sure Python is installed and on PATH.
    pause
    exit /b 1
)

rem 1. Publish local changes, but only if the tests pass.
set "RCM_LOCAL_CHANGES="
for /f "delims=" %%S in ('git status --porcelain') do set "RCM_LOCAL_CHANGES=1"
if defined RCM_LOCAL_CHANGES (
    echo This folder has changes that are not on GitHub yet:
    git status --short
    echo Running the tests before publishing them...
    python -m unittest discover -q > "%TEMP%\rcmtool_tests.log" 2>&1
    if errorlevel 1 (
        echo.
        echo Tests FAILED, so these changes stay on this PC and are not published.
        echo Details: %TEMP%\rcmtool_tests.log
        echo.
    ) else (
        echo Tests passed. Saving the changes...
        git add -A
        git commit -q -m "Local changes from %COMPUTERNAME% (%DATE% %TIME%)"
        if errorlevel 1 echo Could not save the changes with git; they stay on this PC.
    )
)

rem 2. Update from GitHub without ever overwriting local work.
echo Updating RCM Tool from GitHub...
set "RCM_DIRTY="
for /f "delims=" %%S in ('git status --porcelain') do set "RCM_DIRTY=1"
if defined RCM_DIRTY (
    git pull --ff-only
    if errorlevel 1 echo Update skipped: it would touch files changed on this PC. Starting with the local code.
) else (
    git pull --rebase -q
    if errorlevel 1 (
        git rebase --abort >nul 2>nul
        echo Update skipped: it conflicts with this PC's saved changes. Starting with the local code.
    )
)

rem 3. Push saved local changes so testers get them.
set "RCM_AHEAD=0"
for /f %%N in ('git rev-list --count @{u}..HEAD 2^>nul') do set "RCM_AHEAD=%%N"
if not "%RCM_AHEAD%"=="0" (
    echo Publishing %RCM_AHEAD% saved change^(s^) to GitHub...
    git push -q
    if errorlevel 1 echo Publishing failed ^(no network or no GitHub access^). The changes are saved on this PC and will be published next time.
)

rem 4. Start RcmTool as its own program (pythonw has no console window), so
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
