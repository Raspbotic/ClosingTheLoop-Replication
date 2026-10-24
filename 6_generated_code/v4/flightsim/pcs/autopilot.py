"""Autopilot laws. [a: Behaviour B9] [a: L2SYS-REQ-01.11]
Outputs are stick/throttle commands on the wire convention (pitch + = stick back).
"""


def clip(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def altitude_hold(target_alt_m, alt_m, vs_mps, pitch_deg):
    """AH cascade [a: Behaviour B9]."""
    vs_cmd = clip(0.15 * (target_alt_m - alt_m), -5.0, 5.0)
    theta_cmd = clip(1.5 * (vs_cmd - vs_mps), -8.0, 12.0)
    return clip(0.06 * (theta_cmd - pitch_deg), -1.0, 1.0)


def heading_hold(target_hdg_deg, hdg_deg, roll_deg):
    """AP [a: Behaviour B9]."""
    bank_cmd = clip(0.8 * wrap180(target_hdg_deg - hdg_deg), -25.0, 25.0)
    return clip(0.03 * (bank_cmd - roll_deg), -1.0, 1.0)


def autothrottle(target_speed_mps, speed_mps):
    """AT [a: Behaviour B9]."""
    return clip(0.5 + 0.04 * (target_speed_mps - speed_mps), 0.0, 1.0)
