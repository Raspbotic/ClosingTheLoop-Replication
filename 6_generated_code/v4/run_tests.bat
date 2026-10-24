@echo off
rem Runs the regression suite with the fsim venv interpreter by explicit path. [a: Conv]
rem CAVEAT: the suite binds the real federation ports (5005 5006 5007 6006 6007 5550).
rem         Close any running flight simulator windows first.
rem Works from the project root or from tests\. Extra arguments go to pytest (e.g. -m "not slow").
setlocal
set "ROOT=%~dp0"
if /i "%~n0"=="run_tests" if exist "%ROOT%..\flightsim" if not exist "%ROOT%flightsim" set "ROOT=%ROOT%..\"
cd /d "%ROOT%"
if not exist ".venv_fsim\Scripts\python.exe" (echo Run setup_venvs.bat first. & pause & exit /b 1)
set QT_QPA_PLATFORM=offscreen
".venv_fsim\Scripts\python.exe" -m pytest tests -q %*
set RC=%ERRORLEVEL%
echo.
echo pytest exit code %RC%
pause
exit /b %RC%
