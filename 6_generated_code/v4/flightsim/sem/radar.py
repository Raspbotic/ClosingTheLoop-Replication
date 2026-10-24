"""Radar computations. [a: fsim_TA_SEM.sysml#threadMainGui::calcRadarEquation]
[a: fsim_TA_SEM.sysml#threadMainGui::calcBeaconOwnshipRange] [a: L2SYS-REQ-03.05] [a: Behaviour B10]"""
import math

C = 299792458.0
EARTH_R = 6371000.0  # [c] spherical earth for range and placement

# [a: Behaviour B10] defaults
DEFAULT_PT_W = 100e3
DEFAULT_G_DBI = 35.0
DEFAULT_F_HZ = 10e9
DEFAULT_PMIN_DBM = -90.0
DEFAULT_RCS_M2 = 5.0


def dbm_to_w(dbm):
    return 10.0 ** ((dbm - 30.0) / 10.0)


def r_max(pt_w, g_dbi, f_hz, pmin_dbm, rcs_m2):
    """Monostatic R_max = (Pt·G²·λ²·σ / ((4π)³·P_min))^¼. [a: Behaviour B10]"""
    lam = C / f_hz
    g = 10.0 ** (g_dbi / 10.0)
    return (pt_w * g * g * lam * lam * rcs_m2 / ((4.0 * math.pi) ** 3 * dbm_to_w(pmin_dbm))) ** 0.25


def ground_distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def slant_range_m(lat1, lon1, alt1, lat2, lon2, alt2):
    """[c] slant = sqrt(ground² + Δh²); settled by a model definition of slant range."""
    g = ground_distance_m(lat1, lon1, lat2, lon2)
    return math.hypot(g, alt2 - alt1)


def destination(lat, lon, range_m, bearing_deg):
    """Point at range/bearing from (lat, lon). Used for beacon placement (screenshot:
    Initial Range / Initial Theta)."""
    d = range_m / EARTH_R
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0
