@echo off
REM ===========================================================================
REM setup_venvs.bat -- R-3 deliverable.
REM
REM Creates both virtual environments on a fresh Windows host that has only the
REM two interpreters, installs both pinned manifests, and seeds fsim_env.bat.
REM
REM   .venv_fsim   Python 3.14  PCS, orchestrator, SEM, the whole pytest suite
REM   .venv_hotas  Python 3.11  flightsim.hotas_standalone only
REM     [a: D5] [a: contract Sec.5.2]
REM     [a: fsim_TA_Peripherals.sysml] HotasControl.runtime.version = "3.11",
REM         rationale "pygame: no cp314 wheels available" -- independently
REM         confirmed from package metadata by tools/check_wheels.py.
REM
REM No global activate anywhere: every interpreter is invoked by explicit path.
REM     [a: D5] [a: contract Sec.5.2]
REM Idempotent: re-running on an existing tree re-installs the same pins and
REM leaves an existing fsim_env.bat alone.
REM Fails with a message naming the missing interpreter, never a traceback.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

echo ===========================================================================
echo  FlightSim environment setup
echo ===========================================================================
echo.

REM -- report what was found before changing anything -------------------------
echo [1/6] Looking for the required interpreters...
set "FSIM_HAVE_314="
set "FSIM_HAVE_311="

py -3.14 -c "import sys;print(sys.version)" >"%TEMP%\fsim_py314.txt" 2>nul
if not errorlevel 1 set "FSIM_HAVE_314=1"
py -3.11 -c "import sys;print(sys.version)" >"%TEMP%\fsim_py311.txt" 2>nul
if not errorlevel 1 set "FSIM_HAVE_311=1"

if defined FSIM_HAVE_314 (
    set /p FSIM_V314=<"%TEMP%\fsim_py314.txt"
    call echo       found  py -3.14  ->  %%FSIM_V314%%
) else (
    echo       MISSING  py -3.14
)
if defined FSIM_HAVE_311 (
    set /p FSIM_V311=<"%TEMP%\fsim_py311.txt"
    call echo       found  py -3.11  ->  %%FSIM_V311%%
) else (
    echo       MISSING  py -3.11
)
del "%TEMP%\fsim_py314.txt" "%TEMP%\fsim_py311.txt" >nul 2>&1
echo.

if not defined FSIM_HAVE_314 goto :missing314
if not defined FSIM_HAVE_311 goto :missing311

REM -- .venv_fsim -------------------------------------------------------------
echo [2/6] Creating .venv_fsim (Python 3.14)...
if exist ".venv_fsim\Scripts\python.exe" (
    echo       already present, reusing it
) else (
    py -3.14 -m venv .venv_fsim
    if errorlevel 1 goto :venvfail_fsim
)

echo [3/6] Installing requirements_main.txt into .venv_fsim...
".venv_fsim\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :pipfail_fsim
".venv_fsim\Scripts\python.exe" -m pip install -r requirements_main.txt
if errorlevel 1 goto :pipfail_fsim
echo.

REM -- .venv_hotas ------------------------------------------------------------
echo [4/6] Creating .venv_hotas (Python 3.11)...
if exist ".venv_hotas\Scripts\python.exe" (
    echo       already present, reusing it
) else (
    py -3.11 -m venv .venv_hotas
    if errorlevel 1 goto :venvfail_hotas
)

echo [5/6] Installing requirements_hotas.txt into .venv_hotas...
".venv_hotas\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :pipfail_hotas
".venv_hotas\Scripts\python.exe" -m pip install -r requirements_hotas.txt
if errorlevel 1 goto :pipfail_hotas
echo.

REM -- fsim_env.bat -----------------------------------------------------------
echo [6/6] Seeding fsim_env.bat...
if exist "fsim_env.bat" (
    echo       fsim_env.bat already exists, leaving it untouched
) else (
    copy /y "fsim_env.bat.template" "fsim_env.bat" >nul
    if errorlevel 1 (
        echo       could not create fsim_env.bat from the template
    ) else (
        echo       created fsim_env.bat from fsim_env.bat.template
        echo       Edit it only if FlightGear is installed -- it is optional. [a: D16, Sec.5.3]
    )
)
echo.

echo ===========================================================================
echo  Setup complete.
echo.
echo    .venv_fsim   ready   PCS, orchestrator, SEM, pytest suite
echo    .venv_hotas  ready   HOTAS transmitter
echo.
echo  Next:   run_tests.bat            run the regression suite
echo          launch_flightsim.bat     start the four processes
echo.
echo  FlightGear and a physical HOTAS are optional. With neither present the
echo  application and the whole suite still run. [a: Sec.5.3, Sec.3 criterion 6]
echo ===========================================================================
endlocal
exit /b 0

:missing314
echo.
echo  SETUP FAILED: Python 3.14 was not found.
echo.
echo    .venv_fsim requires it (PCS, orchestrator, SEM, pytest suite). [a: D5]
echo    Install CPython 3.14 for Windows x86-64 and make sure the launcher
echo    resolves it, i.e. that this command prints a version:
echo.
echo        py -3.14 --version
echo.
endlocal
exit /b 2

:missing311
echo.
echo  SETUP FAILED: Python 3.11 was not found.
echo.
echo    .venv_hotas requires it. It exists because pygame publishes no cp314
echo    wheel. [a: fsim_TA_Peripherals.sysml, D5]
echo    Install CPython 3.11 for Windows x86-64 and make sure this prints a
echo    version:
echo.
echo        py -3.11 --version
echo.
endlocal
exit /b 3

:venvfail_fsim
echo.
echo  SETUP FAILED: could not create .venv_fsim. Check write permission in
echo  "%CD%" and that "py -3.14 -m venv" works.
endlocal
exit /b 4

:venvfail_hotas
echo.
echo  SETUP FAILED: could not create .venv_hotas. Check write permission in
echo  "%CD%" and that "py -3.11 -m venv" works.
endlocal
exit /b 5

:pipfail_fsim
echo.
echo  SETUP FAILED: installing requirements_main.txt into .venv_fsim failed.
echo  Network access is required once. [a: Sec.5.1]
echo  To check the pins themselves against this target first:
echo      .venv_fsim\Scripts\python.exe tools\check_wheels.py requirements_main.txt --python 3.14 --platform win_amd64
endlocal
exit /b 6

:pipfail_hotas
echo.
echo  SETUP FAILED: installing requirements_hotas.txt into .venv_hotas failed.
echo  Network access is required once. [a: Sec.5.1]
echo  To check the pin itself against this target first:
echo      .venv_hotas\Scripts\python.exe tools\check_wheels.py requirements_hotas.txt --python 3.11 --platform win_amd64
endlocal
exit /b 7
