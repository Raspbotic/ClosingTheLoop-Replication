"""L2SYS-REQ-03.05 radar equation, reference values hand-computed here. [a: Behaviour B10]"""
import math
from flightsim.sem import radar


def test_rmax_hand_computed():
    # Defaults: Pt 100 kW, G 35 dBi, f 10 GHz, Pmin -90 dBm, sigma 5 m^2
    lam = 299792458.0 / 10e9                 # 0.0299792 m
    g = 10 ** 3.5                            # 3162.28
    pmin = 1e-12                             # -90 dBm = 1e-12 W
    num = 100e3 * g * g * lam ** 2 * 5.0     # 4.4938e9
    den = (4 * math.pi) ** 3 * pmin          # 1.98441e-9
    ref = (num / den) ** 0.25                # ~38.79 km
    got = radar.r_max(100e3, 35.0, 10e9, -90.0, 5.0)
    assert abs(got - ref) < 1e-6 * ref
    assert 38000 < got < 40000


def test_scaling_laws():
    base = radar.r_max(100e3, 35.0, 10e9, -90.0, 5.0)
    assert abs(radar.r_max(100e3, 35.0, 10e9, -90.0, 80.0) / base - 2.0) < 1e-9   # 16x sigma -> 2x range
    assert abs(radar.r_max(100e3, 35.0, 10e9, -78.0, 5.0) / base - 10 ** (-12 / 40)) < 1e-9  # +12 dB Pmin
    assert abs(10 ** (-12 / 40) - 0.5) < 0.005  # ~1/2 range


def test_slant_and_destination():
    lat, lon = radar.destination(48.0, 11.0, 10000.0, 90.0)
    assert abs(radar.ground_distance_m(48.0, 11.0, lat, lon) - 10000.0) < 1.0
    assert abs(radar.slant_range_m(48.0, 11.0, 0.0, lat, lon, 3000.0) - math.hypot(10000.0, 3000.0)) < 1.0
