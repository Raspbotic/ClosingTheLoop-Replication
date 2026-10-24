@echo off
REM ==========================================================================
REM launch_flightsim.bat - Flight Simulator demonstrator launcher (Windows)
REM ==========================================================================
REM TRACEABILITY
REM   [a] Starts the six processes of part def FlightSimSystem
REM       (fsim_TA_System.sysml): hotas, pcs, simbus, sem, fgfs, osm.
REM   [a: D5] "Two venvs per the TA PythonRuntime attributes: .venv_fsim
REM       (Python 3.14: PCS, orchestrator, SEM) and .venv_hotas
REM       (Python 3.11: pygame has no cp314 wheels). Launch script activates
REM       per-window; never a global activate. All tooling invokes venv
REM       interpreters by explicit path."
REM   [a: D6] the FlightGear command line, in the commented block below.
REM   [a: D4] port map, owned by flightsim\common\ports.py.
REM ==========================================================================

setlocal
set ROOT=%~dp0
set VENV_FSIM=%ROOT%.venv_fsim
set VENV_HOTAS=%ROOT%.venv_hotas
set PY_FSIM=%VENV_FSIM%\Scripts\python.exe
set PY_HOTAS=%VENV_HOTAS%\Scripts\python.exe

REM -- Local deployment configuration --------------------------------------
REM [b] The FlightGear install location is a deployment parameter, not a
REM     design item: D6 fixes the command line, not the path. fsim_env.bat
REM     exports FSIM_FG_EXECUTABLE / FSIM_FG_HOME / FSIM_FG_AIRCRAFT_DIR /
REM     FSIM_FG_ROOT, which flightsim\common\config.py reads. Copy
REM     fsim_env.bat.template to fsim_env.bat and edit it for this machine.
if exist "%ROOT%fsim_env.bat" (
    call "%ROOT%fsim_env.bat"
) else (
    echo [launch] NOTE: fsim_env.bat not found - FlightGear paths are not
    echo [launch] configured for this machine. Everything except
    echo [launch] "LAUNCH 3D OTW" will still work. To fix:
    echo [launch]     copy fsim_env.bat.template fsim_env.bat
    echo [launch]     then edit fsim_env.bat
)

if not exist "%PY_FSIM%" (
    echo [launch] ERROR: %PY_FSIM% not found.
    echo [launch] Run setup_venvs.bat once to create both virtual
    echo [launch] environments and install their dependencies [a: D5].
    pause
    exit /b 1
)
if not exist "%PY_HOTAS%" (
    echo [launch] ERROR: %PY_HOTAS% not found.
    echo [launch] Run setup_venvs.bat once to create both virtual
    echo [launch] environments and install their dependencies [a: D5].
    pause
    exit /b 1
)

REM -- Report the resolved FlightGear configuration before starting ---------
echo [launch] FlightGear deployment check:
"%PY_FSIM%" -m flightsim.common.config
echo.

REM ==========================================================================
REM  1. Bus Orchestrator  (TA BusOrchestrator, master_node.py) - .venv_fsim
REM     [a: D5] per-window activation, never a global activate.
REM ==========================================================================
start "FSIM - Bus Orchestrator" cmd /k ^
  "cd /d %ROOT% && call "%VENV_FSIM%\Scripts\activate.bat" && "%PY_FSIM%" -m flightsim.master_node --stats"

timeout /t 1 /nobreak >nul

REM ==========================================================================
REM  2. Synthetic Environment Manager  (TA SyntheticEnvironmentMgr) - .venv_fsim
REM ==========================================================================
start "FSIM - SEM" cmd /k ^
  "cd /d %ROOT% && call "%VENV_FSIM%\Scripts\activate.bat" && "%PY_FSIM%" -m flightsim.run_sem"

timeout /t 1 /nobreak >nul

REM ==========================================================================
REM  3. Pilot Control Station  (TA PilotControlStation) - .venv_fsim
REM     Contains threadFlightSim, threadPhysics and threadMapWindow.
REM ==========================================================================
start "FSIM - PCS" cmd /k ^
  "cd /d %ROOT% && call "%VENV_FSIM%\Scripts\activate.bat" && "%PY_FSIM%" -m flightsim.run_pcs"

timeout /t 1 /nobreak >nul

REM ==========================================================================
REM  4. HOTAS transmitter  (TA HotasControl) - .venv_hotas (Python 3.11)
REM     [a: D5] pygame has no cp314 wheels, hence the second venv.
REM     Add --demo for a hardware-free run.
REM ==========================================================================
start "FSIM - HOTAS" cmd /k ^
  "cd /d %ROOT% && call "%VENV_HOTAS%\Scripts\activate.bat" && "%PY_HOTAS%" -m flightsim.hotas_standalone --debug"

REM ==========================================================================
REM  5. FlightGear OTW  (TA FlightGearOTW, fgfs.exe)
REM     Normally launched from the PCS "LAUNCH 3D OTW" SSF button, which
REM     issues exactly this command line [a: D6] with argv[0] resolved from
REM     FSIM_FG_EXECUTABLE / FSIM_FG_HOME. Uncomment to start it here instead.
REM ==========================================================================
REM set FG_AIRPORT=LIML
REM start "FSIM - FlightGear OTW" cmd /k ^
REM   ""%FSIM_FG_EXECUTABLE%" --fdm=null --native-fdm=socket,in,60,,5550,udp ^
REM    --fg-aircraft=%FSIM_FG_AIRCRAFT_DIR% --aircraft=YF-16 --airport=%FG_AIRPORT% ^
REM    --timeofday=noon --httpd=8080 ^
REM    --prop:/input/joysticks/js[0]=0 --prop:/input/joysticks/js[1]=0"

REM ==========================================================================
REM  6. OpenStreetMap tile service (TA OpenStreetMapAPI) is external; the PCS
REM     map window reaches https://tile.openstreetmap.org directly [a: D4].
REM ==========================================================================

echo [launch] All local processes started.
echo [launch] Ports: HOTAS-^>PCS 5005 ^| PCS-^>ORCH 5006 ^| ORCH-^>SEM 5007
echo [launch]        SEM-^>ORCH 6007 ^| ORCH-^>PCS 6006 ^| PCS-^>FG 5550 ^| FG httpd 8080
endlocal
