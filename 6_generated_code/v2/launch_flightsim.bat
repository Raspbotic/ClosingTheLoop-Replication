@echo off
REM ===========================================================================
REM launch_flightsim.bat -- R-9 deliverable.
REM
REM Starts the four processes of the federation, one window each.
REM   [a: D5] "The launch script activates one environment per window; no
REM   global activate; interpreters by explicit path."
REM   [a: fsim_TA_System.sysml] the six parts; FlightGear and OpenStreetMap are
REM   external and are not started here. FlightGear is launched on demand by
REM   the PCS's LAUNCH 3D OTW button. [a: D6] [a: Sec.5.3]
REM
REM Start order: the orchestrator binds 5006 and 6007 and is started first so
REM nothing is sending into a closed port, though UDP means no sender blocks
REM or fails if it is not there yet. [b: D3]
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

REM [a: D16] seed the FlightGear variables. Optional: with fsim_env.bat absent
REM or every line commented out, everything below still runs and only
REM LAUNCH 3D OTW is unavailable. [a: Sec.5.3]
if exist "fsim_env.bat" call "fsim_env.bat"

set "PY_FSIM=%~dp0.venv_fsim\Scripts\python.exe"
set "PY_HOTAS=%~dp0.venv_hotas\Scripts\python.exe"

if not exist "%PY_FSIM%" goto :no_fsim
if not exist "%PY_HOTAS%" goto :no_hotas

echo ===========================================================================
echo  Starting the FlightSim federation
echo ===========================================================================
echo    orchestrator   .venv_fsim    5006 -^> 5007, 6007 -^> 6006
echo    PCS            .venv_fsim    5005 in, 5006 out, 6006 in, 5550 out
echo    SEM            .venv_fsim    5007 in, 6007 out
echo    HOTAS          .venv_hotas   5005 out
echo.
echo  Close any earlier instance first: these are fixed UDP ports. [a: D15]
echo.

REM One window per process, each invoking its OWN interpreter by explicit
REM path. No `activate` anywhere. [a: D5]
start "FlightSim - Orchestrator" cmd /k ""%PY_FSIM%" -m flightsim.master_node"
timeout /t 1 /nobreak >nul

start "FlightSim - PCS" cmd /k ""%PY_FSIM%" -m flightsim.pcs.main_window"
timeout /t 1 /nobreak >nul

start "FlightSim - SEM" cmd /k ""%PY_FSIM%" -m flightsim.run_sem"
timeout /t 1 /nobreak >nul

REM The HOTAS transmitter is the only process in .venv_hotas (Python 3.11),
REM because pygame publishes no cp314 wheel.
REM [a: fsim_TA_Peripherals.sysml] [a: D5]
REM With no physical joystick attached it waits, rescanning at 1 Hz, and the
REM Virtual HOTAS flies the aircraft meanwhile. [a: D10] [a: Sec.5.3]
start "FlightSim - HOTAS" cmd /k ""%PY_HOTAS%" -m flightsim.hotas_standalone"

echo  All four windows started.
endlocal
exit /b 0

:no_fsim
echo.
echo  .venv_fsim was not found at:
echo      %PY_FSIM%
echo  Run setup_venvs.bat first.
endlocal
exit /b 2

:no_hotas
echo.
echo  .venv_hotas was not found at:
echo      %PY_HOTAS%
echo  Run setup_venvs.bat first.
endlocal
exit /b 3
