"""Pilot Control Station. [a: fsim_TA_PCS.sysml]

Three threads per the TA:
    threadFlightSim   main_window.py (UI manager) + io_manager.py (background I/O)
    threadPhysics     physics.py
    threadMapWindow   map_window.py

The Qt-free model layers -- airports, autopilot, telemetry, warnings, the
slippy-map arithmetic and the physics itself -- are separate modules so they are
verifiable without a display. [b: R-7 "keep the model layer of both free of Qt"]
"""
