"""FGNetFDM packing for the visual-system feed. [a: Behaviour B13]

Version 24 [a: fsim_TA_IF_Requirements.sysml#req_pcsNativeFdmOutPort "FGNetFDM v24"].
Layout and network byte order measured against FlightGear src/Network/net_fdm.hxx and
native_structs.cxx (htonl/htond/htonf) — finding F11. Size 408 bytes.
"""
import math
import struct

FG_NET_FDM_VERSION = 24
FG_MAX_ENGINES, FG_MAX_WHEELS, FG_MAX_TANKS = 4, 3, 4

_FMT = (">II"          # version, padding
        "ddd"          # longitude, latitude (rad), altitude (m)
        "22f"          # agl .. slip_deg
        "I4I" + "36f"  # num_engines, eng_state[4], 9 float engine arrays x4
        + "I4f"        # num_tanks, fuel_quantity[4]
        + "I3I9f"      # num_wheels, wow[3], gear_pos/steer/compression[3]
        + "Iif"        # cur_time, warp, visibility
        + "10f")       # control surfaces
PACKER = struct.Struct(_FMT)
FDM_SIZE = PACKER.size  # 408


def pack(t):
    """t: telemetry dict from ThreadPhysics (SI units, degrees)."""
    d2r = math.radians
    agl = t.get("agl_m", 0.0)
    floats22 = [agl, d2r(t["roll_deg"]), d2r(t["pitch_deg"]), d2r(t["heading_deg"]),
                d2r(t.get("alpha_deg", 0.0)), d2r(t.get("beta_deg", 0.0)),
                0.0, 0.0, 0.0,
                t.get("vcas_kts", 0.0), t.get("v_speed_mps", 0.0) / 0.3048,  # vcas kt, climb_rate ft/s [c: net_fdm.hxx comments]
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    eng_state = [2 if t.get("engine_state") == "RUNNING" else (1 if t.get("engine_state") == "STARTING" else 0), 0, 0, 0]
    rpm = [t.get("n2_pct", 0.0) * 100.0, 0, 0, 0]
    eng_floats = rpm + [0.0] * 32
    fuel = [t.get("fuel_lbs", 0.0) / 6.7, 0.0, 0.0, 0.0]  # [c] gallons at 6.7 lb/gal
    wow = [int(t.get("wow", False))] * 3
    gpos = [t.get("gear_pos", 1.0)] * 3
    steer = [t.get("steer_pos", 0.0), 0.0, 0.0]
    comp = [0.0] * 3
    ctl = [t.get("elevator_cmd", 0.0), 0.0, 0.0, 0.0, t.get("aileron_cmd", 0.0), -t.get("aileron_cmd", 0.0),
           t.get("rudder_cmd", 0.0), t.get("steer_pos", 0.0), t.get("speedbrake_pos", 0.0), 0.0]
    return PACKER.pack(FG_NET_FDM_VERSION, 0,
                       d2r(t["lon_deg"]), d2r(t["lat_deg"]), t["alt_m"],
                       *floats22,
                       1, *eng_state, *eng_floats,
                       1, *fuel,
                       3, *wow, *gpos, *steer, *comp,
                       int(t.get("sim_time_s", 0.0)), 0, 10000.0,
                       *ctl)
