@echo off
setlocal
cd /d "%~dp0"

echo Reports currently waiting to be submitted:
git status --short reports
echo.
set /p CONFIRM="Commit and push these reports to GitHub? Type YES to continue: "
if /I not "%CONFIRM%"=="YES" (
    echo No reports were submitted.
    pause
    exit /b 0
)

git add reports
git diff --cached --quiet
if not errorlevel 1 (
    echo No new report files were found.
    pause
    exit /b 0
)

git commit -m "Add controller test reports"
if errorlevel 1 (
    echo Commit failed.
    pause
    exit /b 1
)

git push origin master
if errorlevel 1 (
    echo Push failed. The report commit is still saved locally.
    pause
    exit /b 1
)

echo Reports successfully pushed to GitHub.
pause
