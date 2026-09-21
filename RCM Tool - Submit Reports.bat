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

for /f "delims=" %%A in ('git config user.name 2^>nul') do set "GIT_NAME=%%A"
for /f "delims=" %%A in ('git config user.email 2^>nul') do set "GIT_EMAIL=%%A"
if not defined GIT_NAME (
    echo Git needs the tester name for the report commit.
    set /p GIT_NAME="Enter tester name: "
    if not defined GIT_NAME set "GIT_NAME=RCM Tester"
    git config user.name "%GIT_NAME%"
)
if not defined GIT_EMAIL (
    echo Git needs an email for the report commit.
    set /p GIT_EMAIL="Enter tester email (GitHub noreply email is okay): "
    if not defined GIT_EMAIL set "GIT_EMAIL=rcm-tester@users.noreply.github.com"
    git config user.email "%GIT_EMAIL%"
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
