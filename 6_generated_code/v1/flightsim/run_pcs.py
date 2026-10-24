"""
flightsim/run_pcs.py
====================
Entry point of the Pilot Control Station process.

[a] Launches part def PilotControlStation (fsim_TA_PCS.sysml) with its
    three thread parts: threadFlightSim (main_window.py), threadPhysics
    (physics.py) and threadMapWindow (map_window.py).
[a: D5] runs under .venv_fsim (Python 3.14).
"""

from flightsim.pcs.main_window import main

if __name__ == "__main__":
    raise SystemExit(main())
