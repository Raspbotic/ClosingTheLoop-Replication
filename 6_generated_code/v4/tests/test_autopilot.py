"""L2SYS-REQ-01.11 autopilot laws against a hand computation of Behaviour B9."""
from flightsim.pcs import autopilot as a
from flightsim.pcs.physics import ThreadPhysics


def test_law_values():
    # AH: alt err 20 m -> vs_cmd = min(3.0, 5) = 3.0; theta_cmd = 1.5*(3.0-1.0) = 3.0; pitch = 0.06*(3.0-2.0) = 0.06
    assert abs(a.altitude_hold(1020.0, 1000.0, 1.0, 2.0) - 0.06) < 1e-12
    # saturation: alt err 1000 -> vs_cmd 5; theta_cmd = clip(1.5*5, -8, 12) = 7.5; pitch = clip(0.06*(7.5+30)) = 1
    assert a.altitude_hold(2000.0, 1000.0, 0.0, -30.0) == 1.0
    # AP: hdg err -20 (350->10 wraps to +20) -> bank_cmd = 16; roll = 0.03*(16-6) = 0.3
    assert abs(a.heading_hold(10.0, 350.0, 6.0) - 0.3) < 1e-12
    assert a.heading_hold(180.0, 0.1, 0.0) == 0.75  # bank clip 25 -> 0.03*25
    # AT: throttle = 0.5 + 0.04*(10) = 0.9; clipped at 1
    assert abs(a.autothrottle(210.0, 200.0) - 0.9) < 1e-12
    assert a.autothrottle(300.0, 200.0) == 1.0 and a.autothrottle(0.0, 200.0) == 0.0


def test_sync_on_engage():
    """B9: engaging captures the current value as target."""
    p = ThreadPhysics(send_fdm=False)
    p.send("set_position", dict(lat_deg=48.5, lon_deg=11.5, alt_m=3000.0, speed_mps=200.0, heading_deg=123.0))
    p.send("autopilot", dict(ap=True, ah=True, at=True))
    p.step()
    t = p.latest()
    p.stop()
    assert abs(t["target_alt_m"] - 3000) < 5 and abs(t["target_heading_deg"] - 123) < 1 and abs(t["target_speed_mps"] - 200) < 3
