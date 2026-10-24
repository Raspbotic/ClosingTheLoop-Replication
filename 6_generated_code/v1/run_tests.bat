@echo off
REM ==========================================================================
REM run_tests.bat - regression suite runner
REM ==========================================================================
REM TRACEABILITY
REM   [a: D15] "runner script invokes the venv python by path and pauses on
REM       exit"; "offscreen Qt (QT_QPA_PLATFORM=offscreen)"; "tests bind the
REM       real UDP ports (document the 'close running instances' caveat)".
REM   Works from the repository root or from tests\ ; forwards pytest args.
REM
REM   Examples:
REM       run_tests.bat                       all tests
REM       run_tests.bat -m "not slow"         skip the slow rows
REM       run_tests.bat tests\test_sem.py -v  one module, verbose
REM ==========================================================================

setlocal
set HERE=%~dp0
REM [SI-1] run_tests.bat lives at the repository root, so %HERE% IS the
REM root. The previous two-branch form (which also handled a copy placed
REM inside tests\, per the D15 wording "works from root or tests\") left
REM ROOT empty when edited out, and `cd /d ""` then failed with "The
REM filename, directory name, or volume label syntax is incorrect."
set ROOT=%HERE%
set PY_FSIM=%ROOT%.venv_fsim\Scripts\python.exe

if not exist "%PY_FSIM%" (
    echo [tests] ERROR: %PY_FSIM% not found.
    echo [tests] Run setup_venvs.bat once to create both virtual
    echo [tests] environments and install their dependencies [a: D5].
    pause
    exit /b 1
)

REM [b] The suite must NOT depend on this machine's FlightGear installation:
REM     the D6 conformance tests assert the command line, not a local path,
REM     and they pin the FSIM_FG_* variables themselves. fsim_env.bat is
REM     therefore deliberately NOT sourced here.

REM [a: D15] offscreen Qt, so the GUI rows run headless.
set QT_QPA_PLATFORM=offscreen

echo [tests] CAVEAT: the suite binds the real UDP ports 5005/5006/5007/
echo [tests] 6006/6007/5550. CLOSE ANY RUNNING FLIGHTSIM INSTANCE first,
echo [tests] or the binds will fail with EADDRINUSE.
echo.

cd /d "%ROOT%"
"%PY_FSIM%" -m pytest tests %*
set RC=%ERRORLEVEL%

echo.
echo [tests] pytest exit code: %RC%
pause
endlocal & exit /b %RC%
