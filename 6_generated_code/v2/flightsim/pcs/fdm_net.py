"""FGNetFDM version 24 packing. [a: D4] [a: L2SYS-REQ-01.03b]

[a: D4] "PCS->FlightGear native-fdm 5550 (FGNetFDM version 24, 408 bytes,
big-endian, decimated to about 60 Hz)".

The three facts D4 states -- **version 24**, **408 bytes**, **big-endian** --
are the contract this module is written against, and the layout below is the
one that satisfies all three. `test_physics.py::test_fgnetfdm_is_version_24_and_
408_bytes_big_endian` asserts each of them against the packed bytes.

The field layout is **[c]**: it is FlightGear's `net_fdm.hxx` struct, which is
not a supplied artifact. What would settle it: the `net_fdm.hxx` of the
FlightGear the practitioner installs, or an observed OTW view that tracks
correctly (manual row M-3 / HW-4). The *size* is not a guess -- 408 bytes is
D4's own figure and the layout below is checked against it at import.

Nothing here requires FlightGear. The stream is written whether or not anything
is listening. [a: R-5]
"""

from __future__ import annotations

import math
import struct

#: [a: D4] "FGNetFDM version 24"
FG_NET_FDM_VERSION = 24

#: [a: D4] "408 bytes"
FG_NET_FDM_SIZE = 408

#: FlightGear's compile-time array bounds. [c] -- part of the same struct.
FG_MAX_ENGINES = 4
FG_MAX_TANKS = 4
FG_MAX_WHEELS = 3

# Big-endian [a: D4], in the order net_fdm.hxx declares:
#   >   big-endian, no alignment padding                       [a: D4]
#   I   version
#   I   padding
#   3d  longitude, latitude (radians), altitude (metres ASL)
#   22f agl, phi, theta, psi, alpha, beta, phidot, thetadot, psidot,
#       vcas, climb_rate, v_north, v_east, v_down,
#       v_body_u, v_body_v, v_body_w,
#       A_X_pilot, A_Y_pilot, A_Z_pilot, stall_warning, slip_deg
#   I   num_engines
#   4I  eng_state[4]
#   36f rpm, fuel_flow, fuel_px, egt, cht, mp_osi, tit, oil_temp, oil_px  (4 each)
#   I   num_tanks
#   4f  fuel_quantity[4]
#   I   num_wheels
#   3I  wow[3]
#   9f  gear_pos[3], gear_steer[3], gear_compression[3]
#   I   cur_time
#   i   warp
#   f   visibility
#   10f elevator, elevator_trim_tab, left_flap, right_flap, left_aileron,
#       right_aileron, rudder, nose_wheel, speedbrake, spoilers
_FORMAT = ">II3d22fI4I36fI4fI3I9fIif10f"

_STRUCT = struct.Struct(_FORMAT)

# D4 states the size; the layout must produce it. Asserted at import so a
# layout edit that breaks it fails immediately rather than on the wire.
assert _STRUCT.size == FG_NET_FDM_SIZE, (
    f"FGNetFDM layout packs to {_STRUCT.size} bytes; D4 states {FG_NET_FDM_SIZE}"
)

FT_PER_M = 3.280839895013123


def pack(
    *,
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
    agl_m: float,
    phi_deg: float,
    theta_deg: float,
    psi_deg: float,
    alpha_deg: float = 0.0,
    beta_deg: float = 0.0,
    vcas_kts: float = 0.0,
    climb_rate_fps: float = 0.0,
    rpm_pct: float = 0.0,
    fuel_lbs: float = 0.0,
    engine_running: bool = False,
    on_ground: bool = True,
    gear_pos: float = 1.0,
    elevator: float = 0.0,
    left_aileron: float = 0.0,
    right_aileron: float = 0.0,
    rudder: float = 0.0,
    nose_wheel: float = 0.0,
    speedbrake: float = 0.0,
    cur_time: int = 0,
) -> bytes:
    """One FGNetFDM v24 datagram.

    Angles arrive in **degrees** (D19's units) and are converted to the
    **radians** the struct carries; altitude arrives in metres and stays in
    metres, which is what the struct's `altitude` and `agl` fields use.
    [a: D19] [c: net_fdm.hxx]
    """
    lon_rad = math.radians(lon_deg)
    lat_rad = math.radians(lat_deg)

    engines = [1 if engine_running else 0] + [0] * (FG_MAX_ENGINES - 1)
    rpm = [rpm_pct] + [0.0] * (FG_MAX_ENGINES - 1)
    zeros4 = [0.0] * FG_MAX_ENGINES
    fuel_quantity = [fuel_lbs] + [0.0] * (FG_MAX_TANKS - 1)
    wow = [1 if on_ground else 0] * FG_MAX_WHEELS
    gear_positions = [gear_pos] * FG_MAX_WHEELS
    gear_steer = [nose_wheel] + [0.0] * (FG_MAX_WHEELS - 1)
    gear_compression = [0.0] * FG_MAX_WHEELS

    values: list = [
        FG_NET_FDM_VERSION,
        0,                                  # padding
        lon_rad, lat_rad, float(alt_m),
        float(agl_m),
        math.radians(phi_deg),
        math.radians(theta_deg),
        math.radians(psi_deg),
        math.radians(alpha_deg),
        math.radians(beta_deg),
        0.0, 0.0, 0.0,                      # phidot, thetadot, psidot
        float(vcas_kts),
        float(climb_rate_fps),
        0.0, 0.0, 0.0,                      # v_north, v_east, v_down
        0.0, 0.0, 0.0,                      # v_body u, v, w
        0.0, 0.0, 0.0,                      # A_X/Y/Z pilot
        0.0,                                # stall_warning
        0.0,                                # slip_deg
        1,                                  # num_engines
        *engines,
        *rpm,
        *zeros4,                            # fuel_flow
        *zeros4,                            # fuel_px
        *zeros4,                            # egt
        *zeros4,                            # cht
        *zeros4,                            # mp_osi
        *zeros4,                            # tit
        *zeros4,                            # oil_temp
        *zeros4,                            # oil_px
        1,                                  # num_tanks
        *fuel_quantity,
        FG_MAX_WHEELS,                      # num_wheels
        *wow,
        *gear_positions,
        *gear_steer,
        *gear_compression,
        int(cur_time) & 0xFFFFFFFF,
        0,                                  # warp
        0.0,                                # visibility
        float(elevator), 0.0, 0.0, 0.0,     # elevator, trim tab, flaps
        float(left_aileron), float(right_aileron),
        float(rudder), float(nose_wheel),
        float(speedbrake), 0.0,             # speedbrake, spoilers
    ]
    return _STRUCT.pack(*values)


def unpack_version(data: bytes) -> int:
    """The version field of a packed datagram, read big-endian. [a: D4]

    Used by the suite to assert that the stream really is version 24 when read
    the way FlightGear reads it, and is *not* 24 when read little-endian --
    which is what makes "big-endian" a checked claim rather than a comment.
    """
    if len(data) < 4:
        raise ValueError("datagram too short to carry a version")
    return struct.unpack(">I", data[:4])[0]
