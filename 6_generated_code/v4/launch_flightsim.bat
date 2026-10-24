@echo off
rem Launches the federation, one window per process, each activating its own venv. [a: Conv]
rem Order: orchestrator, SEM, PCS, HOTAS. The visual system (FlightGear) is launched from the
rem PCS "LAUNCH 3D OTW" button [a: Behaviour B4]; its location comes from fsim_env.bat.
setlocal
cd /d "%~dp0"
if not exist ".venv_fsim\Scripts\python.exe" (echo Run setup_venvs.bat first. & pause & exit /b 1)
if not exist ".venv_hotas\Scripts\python.exe" (echo Run setup_venvs.bat first. & pause & exit /b 1)
if exist "fsim_env.bat" call "fsim_env.bat"

start "FSIM Orchestrator" cmd /k "call .venv_fsim\Scripts\activate.bat && python -m flightsim.orch.master_node"
start "FSIM SEM"          cmd /k "call .venv_fsim\Scripts\activate.bat && python -m flightsim.sem.run_sem"
start "FSIM PCS"          cmd /k "call .venv_fsim\Scripts\activate.bat && python -m flightsim.pcs.app"
rem Physical HOTAS; add --demo for the synthetic source, --debug for printouts.
start "FSIM HOTAS"        cmd /k "call .venv_hotas\Scripts\activate.bat && python -m flightsim.hotas.hotas_standalone"

rem Visual system (optional) is started by the PCS. Manual equivalent, for reference only:
rem "%FSIM_FGFS_EXE%" --fdm=null --native-fdm=socket,in,60,,5550,udp --fg-aircraft=%FSIM_FGFS_AIRCRAFT_DIR% --aircraft=f16 --airport=EDDM --timeofday=noon --httpd=8080 --prop:/input/joysticks/js[0]=0 --prop:/input/joysticks/js[1]=0
endlocal
