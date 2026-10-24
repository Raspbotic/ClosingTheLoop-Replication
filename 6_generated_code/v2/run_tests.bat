@echo off
REM ===========================================================================
REM run_tests.bat -- R-9 deliverable.
REM
REM [a: D15] "The runner invokes the environment's Python by path and pauses on
REM exit. The suite runs in .venv_fsim."
REM
REM Forwards every argument to pytest, so:
REM     run_tests.bat -q
REM     run_tests.bat -m "not slow"
REM     run_tests.bat tests/test_physics.py -k engine
REM
REM CAVEAT [a: D15]: the suite binds the real UDP ports 5005, 5006, 5007, 6006,
REM 6007 and 5550. Close any running instance of the application first, or
REM those rows will fail on "address already in use".
REM
REM No FlightGear and no HOTAS are needed: the whole suite runs without either.
REM [a: Sec.3 criterion 6]
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "PY_FSIM=%~dp0.venv_fsim\Scripts\python.exe"
if not exist "%PY_FSIM%" goto :no_venv

REM [a: D15] offscreen Qt, so no window appears and the suite runs headless.
set "QT_QPA_PLATFORM=offscreen"

echo ===========================================================================
echo  FlightSim regression suite
echo ===========================================================================
echo  Interpreter: %PY_FSIM%
echo  Close any running FlightSim instance first (the suite binds the real
echo  UDP ports). [a: D15]
echo.

"%PY_FSIM%" -m pytest %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo  RESULT: PASSED
) else (
    echo  RESULT: FAILED  ^(pytest exit code %RC%^)
)
echo.
pause
endlocal & exit /b %RC%

:no_venv
echo.
echo  .venv_fsim was not found at:
echo      %PY_FSIM%
echo  Run setup_venvs.bat first.
echo.
pause
endlocal
exit /b 2
