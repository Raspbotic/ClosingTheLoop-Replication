@echo off
rem Creates one venv per interpreter the model names. [a: Conv]
rem .venv_fsim  : Python 3.14 (PCS, orchestrator, SEM)  [a: fsim_TA_PCS.sysml runtime]
rem .venv_hotas : Python 3.11 (HOTAS)                    [a: fsim_TA_Peripherals.sysml runtime]
rem Idempotent. Every interpreter is invoked by explicit path; no global activate.
setlocal
cd /d "%~dp0"

py -3.14 -c "import sys" >nul 2>&1 || (echo ERROR: Python 3.14 not found. Install it and retry. & goto :fail)
py -3.11 -c "import sys" >nul 2>&1 || (echo ERROR: Python 3.11 not found. Install it and retry. & goto :fail)

if not exist ".venv_fsim\Scripts\python.exe" py -3.14 -m venv .venv_fsim || goto :fail
if not exist ".venv_hotas\Scripts\python.exe" py -3.11 -m venv .venv_hotas || goto :fail

".venv_fsim\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv_fsim\Scripts\python.exe" -m pip install -r requirements_main.txt || goto :fail
".venv_hotas\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv_hotas\Scripts\python.exe" -m pip install -r requirements_hotas.txt || goto :fail

if not exist "fsim_env.bat" copy /y "fsim_env.bat.template" "fsim_env.bat" >nul

echo.
echo Environments ready:
".venv_fsim\Scripts\python.exe" -c "import sys,jsbsim,PyQt5.QtCore as q;print(' fsim ',sys.version.split()[0],'jsbsim',jsbsim.__version__,'Qt',q.QT_VERSION_STR)"
".venv_hotas\Scripts\python.exe" -c "import sys,os;os.environ['PYGAME_HIDE_SUPPORT_PROMPT']='1';import pygame;print(' hotas',sys.version.split()[0],'pygame',pygame.version.ver)"
pause
exit /b 0
:fail
echo SETUP FAILED.
pause
exit /b 1
