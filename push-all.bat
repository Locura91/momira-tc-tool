@echo off
REM ============================================================================
REM push-all.bat - commit and push EVERY changed file in this repo.
REM
REM WHY THIS EXISTS: the platform is one app split across fifteen files, and a
REM commit that includes some of them and not others deploys a half-version.
REM That has now happened twice. The symptom is not an obvious error - it is a
REM traceback whose line numbers point at code that has nothing to do with the
REM fault, because Streamlit draws the source from whatever is on disk now
REM while running the older module. Hours go into diagnosing a partial push.
REM
REM GitHub Desktop lists every changed file with its own tick box. This does the
REM same job without the ticking: `git add -A` stages everything that changed,
REM including new files, which are the ones most easily missed.
REM
REM SAFE TO DELETE. It does nothing GitHub Desktop cannot do - it just cannot
REM leave a file behind.
REM ============================================================================

cd /d "%~dp0"

echo.
echo === What has changed ===
git status --short
echo.

REM Nothing to do? Say so rather than making an empty commit.
git diff --quiet && git diff --cached --quiet
if %errorlevel%==0 (
    echo Nothing has changed - nothing to commit.
    echo.
    pause
    exit /b 0
)

set MSG=%*
if "%MSG%"=="" set MSG=Update Momira platform

echo === Staging every changed file ===
git add -A
if errorlevel 1 goto failed

echo === Committing ===
git commit -m "%MSG%"
if errorlevel 1 goto failed

echo === Pushing ===
git push
if errorlevel 1 goto failed

echo.
echo Done. Every changed file was committed and pushed.
echo Wait for Streamlit to redeploy, then check the Build version at the
echo bottom of the page and make sure no red banner appears at the top.
echo.
pause
exit /b 0

:failed
echo.
echo *** Something went wrong - see the message above. ***
echo Nothing further was pushed. The most common causes are a login prompt
echo git could not show, or a conflict that needs resolving in GitHub Desktop.
echo.
pause
exit /b 1
