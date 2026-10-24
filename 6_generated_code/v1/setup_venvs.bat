@echo off
REM ==========================================================================
REM setup_venvs.bat - first-run bootstrap
REM ==========================================================================
REM Creates BOTH virtual environments and installs their pinned dependencies,
REM then prints the resolved FlightGear deployment configuration.
REM
REM TRACEABILITY
REM   [a: D5] "Two venvs per the TA PythonRuntime attributes: .venv_fsim
REM       (Python 3.14: PCS, orchestrator, SEM) and .venv_hotas (Python 3.11:
REM       pygame has no cp314 wheels) ... All tooling invokes venv
REM       interpreters by explicit path."
REM   [a] fsim_TA_PCS.sysml / fsim_TA_Orch.sysml / fsim_TA_SEM.sysml declare
REM       PythonRuntime version "3.14"; fsim_TA_Peripherals.sysml declares
REM       "3.11" with rationale "pygame: no cp314 wheels available".
REM
REM Run once:   setup_venvs.bat
REM ==========================================================================

setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

echo ==========================================================================
echo  FlightSim demonstrator - first-run setup
echo ==========================================================================
echo.

REM -- 1. .venv_fsim : Python 3.14 [a: D5] ----------------------------------
if exist "%ROOT%.venv_fsim\Scripts\python.exe" (
    echo [setup] .venv_fsim already exists - skipping creation.
) else (
    echo [setup] Creating .venv_fsim with Python 3.14 [a: D5]...
    py -3.14 -m venv .venv_fsim
    if errorlevel 1 (
        echo [setup] ERROR: could not create .venv_fsim with py -3.14.
        echo [setup] Install Python 3.14, or edit this script to use the
        echo [setup] interpreter you have. The TA PythonRuntime attribute
        echo [setup] for PCS/orchestrator/SEM is "3.14".
        pause
        exit /b 1
    )
)
echo [setup] Installing requirements_main.txt into .venv_fsim...
"%ROOT%.venv_fsim\Scripts\python.exe" -m pip install --upgrade pip
"%ROOT%.venv_fsim\Scripts\python.exe" -m pip install -r requirements_main.txt
if errorlevel 1 (
    echo [setup] ERROR: dependency installation failed for .venv_fsim.
    pause
    exit /b 1
)
echo.

REM -- 2. .venv_hotas : Python 3.11 [a: D5] ---------------------------------
if exist "%ROOT%.venv_hotas\Scripts\python.exe" (
    echo [setup] .venv_hotas already exists - skipping creation.
) else (
    echo [setup] Creating .venv_hotas with Python 3.11 [a: D5]...
    py -3.11 -m venv .venv_hotas
    if errorlevel 1 (
        echo [setup] ERROR: could not create .venv_hotas with py -3.11.
        echo [setup] Python 3.11 is required because pygame has no cp314
        echo [setup] wheels [a: fsim_TA_Peripherals.sysml rationale].
        pause
        exit /b 1
    )
)
echo [setup] Installing requirements_hotas.txt into .venv_hotas...
"%ROOT%.venv_hotas\Scripts\python.exe" -m pip install --upgrade pip
"%ROOT%.venv_hotas\Scripts\python.exe" -m pip install -r requirements_hotas.txt
if errorlevel 1 (
    echo [setup] ERROR: dependency installation failed for .venv_hotas.
    pause
    exit /b 1
)
echo.

REM -- 3. local deployment configuration ------------------------------------
if not exist "%ROOT%fsim_env.bat" (
    echo [setup] Creating fsim_env.bat from the template.
    copy /y "%ROOT%fsim_env.bat.template" "%ROOT%fsim_env.bat" >nul
    echo [setup] ^>^> EDIT fsim_env.bat and set the FlightGear paths for
    echo [setup]    this machine, then re-run setup_venvs.bat to re-check.
) else (
    echo [setup] fsim_env.bat already exists - keeping it.
)
call "%ROOT%fsim_env.bat"
echo.

echo ==========================================================================
echo  FlightGear deployment check
echo ==========================================================================
"%ROOT%.venv_fsim\Scripts\python.exe" -m flightsim.common.config
if errorlevel 1 (
    echo.
    echo [setup] Setup is COMPLETE for the simulation, but FlightGear is not
    echo [setup] reachable yet: everything except "LAUNCH 3D OTW" will work.
    echo [setup] Fix the paths in fsim_env.bat and re-run this script.
) else (
    echo.
    echo [setup] Setup COMPLETE. Start the demonstrator with launch_flightsim.bat
)
echo.
pause
endlocal
