"""Physics rows: 01.02, 01.02b, 01.04, 01.07, B6, B7, B13, SSF backend.
[a: fsim_TA_PCS_ThreadPhysics.sysml#ThreadPhysics]"""
import math
import queue
import socket
import time

import pytest

from flightsim.common import ports
from flightsim.pcs.physics import ThreadPhysics
from flightsim.pcs.airfields import SEED
from flightsim.pcs import fgnetfdm


def steps(p, seconds):
    for _ in range(int(round(seconds * 120))):
        p.step()
    return p.latest()


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@pytest.fixture
def phys():
    p = ThreadPhysics(send_fdm=False)
    yield p
    p.stop()


def started(p):
    p.send("run"); p.send("engine", True)
    steps(p, 30)
    assert p.latest()["engine_state"] == "RUNNING"
    return p


def test_step_rate_120hz():
    """L2SYS-REQ-01.02b: 120 FDM steps per simulated second; real-time factor 0.85-1.15 over 2 s."""
    p = ThreadPhysics(send_fdm=False)
    p.send("run")
    for _ in range(120):
        p.step()
    assert p.frame_count == 120 and abs(p.sim_time - 1.0) < 1e-9
    p.start()
    t0, s0 = time.monotonic(), p.sim_time
    time.sleep(2.0)
    wall, sim = time.monotonic() - t0, p.sim_time - s0
    p.stop()
    assert 0.85 <= sim / wall <= 1.15


def test_state_responds_to_controls():
    """L2SYS-REQ-01.02b: position, attitude, velocity computed from received control inputs."""
    p = ThreadPhysics(send_fdm=False)
    p.send("set_position", dict(lat_deg=48.5, lon_deg=11.5, alt_m=3000.0, speed_mps=200.0, heading_deg=90.0))
    p.step()  # set position is freeze-gated
    p.send("run")
    t0 = steps(p, 0.1)
    p.send("controls", dict(roll=0.5))
    t1 = steps(p, 1.0)
    assert t1["roll_deg"] > t0["roll_deg"] + 5
    assert t1["lon_deg"] > t0["lon_deg"]  # moving east
    p.send("controls", dict(roll=0.0, pitch=0.5))
    q0 = p.latest()["pitch_deg"]
    t2 = steps(p, 1.0)
    assert t2["pitch_deg"] > q0  # stick back = nose up [a: Behaviour B8]
    p.stop()


def test_virtual_ownship_exists_in_pcs(qapp):
    """L2SYS-REQ-01.02: the PCS application owns a running ThreadPhysics."""
    from flightsim.pcs.app import PilotControlStation
    pcs = PilotControlStation(start_network=False)
    try:
        assert isinstance(pcs.physics, ThreadPhysics)
        assert pcs.physics._thread is not None and pcs.physics._thread.is_alive()
    finally:
        pcs.shutdown()


@pytest.mark.parametrize("icao", sorted(SEED))
def test_ground_start_each_airfield(icao):
    """L2SYS-REQ-01.04 / B6: ground start at the selected airfield threshold."""
    p = ThreadPhysics(icao=icao, send_fdm=False)
    af = SEED[icao]
    t = p.latest()
    assert haversine_m(t["lat_deg"], t["lon_deg"], af.lat_deg, af.lon_deg) < 50
    assert t["agl_m"] < 3.0 and t["wow"]
    assert abs(((t["heading_deg"] - af.runway_hdg_deg + 180) % 360) - 180) < 2
    assert t["speed_mps"] < 1.0
    assert t["engine_state"] == "OFF" and t["park_brake"] and t["gear_down"]  # [a: Behaviour B6]
    p.stop()


def test_engine_off_no_fuel_burn(phys):
    """L2SYS-REQ-01.07: engine initializes OFF with zero fuel consumption."""
    phys.send("run")
    f0 = steps(phys, 0.5)["fuel_lbs"]
    t = steps(phys, 10)
    assert t["engine_state"] == "OFF" and abs(t["fuel_lbs"] - f0) < 0.01 and t["n2_pct"] < 1


def test_start_at_idle_burns_fuel(phys):
    """L2SYS-REQ-01.07: ENGINE ON at idle spools to idle and fuel consumption commences."""
    phys.send("run")
    f0 = steps(phys, 0.5)["fuel_lbs"]
    phys.send("engine", True)
    n2 = [steps(phys, 5)["n2_pct"] for _ in range(6)]
    assert n2 == sorted(n2) and n2[0] < n2[-1]  # spooling
    t = phys.latest()
    assert t["engine_state"] == "RUNNING" and t["n2_pct"] > 45
    assert t["fuel_lbs"] < f0 - 0.5


def test_start_refused_above_idle_gate(phys):
    """B6 idle gate: start refused above 10 % throttle."""
    phys.send("run")
    phys.send("controls", dict(throttle=0.5))
    phys.send("engine", True)
    t = steps(phys, 5)
    assert t["engine_state"] == "OFF"
    assert "REFUSED" in phys.messages.get_nowait()


def test_airborne_setpos_engine_on(phys):
    """C-B6: airborne set-position implies engine ON and parking brake released."""
    phys.send("set_position", dict(lat_deg=48.4, lon_deg=11.8, alt_m=2000.0, speed_mps=180.0, heading_deg=270.0))
    phys.step()
    t = phys.latest()
    assert t["engine_state"] == "RUNNING" and not t["park_brake"]
    assert abs(t["alt_m"] - 2000) < 5 and abs(t["speed_mps"] - 180) < 3 and abs(t["heading_deg"] - 270) < 1
    assert abs(t["lat_deg"] - 48.4) < 1e-4 and abs(t["lon_deg"] - 11.8) < 1e-4


def test_terrain_slew_limit(phys):
    """C-B7: terrain elevation slewed at most 1 ft per physics frame toward the target."""
    phys.send("run")
    steps(phys, 0.1)
    e0 = phys.latest()["terrain_ft"]
    phys.send("terrain", (e0 + 10.0) * 0.3048)
    vals = []
    for _ in range(15):
        phys.step()
        vals.append(phys.latest()["terrain_ft"])
    diffs = [b - a for a, b in zip([e0] + vals, vals)]
    assert all(d <= 1.0 + 1e-6 for d in diffs)
    assert abs(vals[-1] - (e0 + 10.0)) < 1e-3


def test_visual_feed_decimated_60hz(udp_receiver):
    """C-B13: native-FDM packets on 5550 at 60 Hz (decimated from 120 Hz), 408 bytes."""
    rx = udp_receiver(ports.NATIVE_FDM_PORT)
    p = ThreadPhysics()
    rx.collect(0.05)
    p.send("run")
    steps(p, 1.0)
    data = rx.collect(0.2)
    p.stop()
    assert 58 <= len(data) <= 62
    assert all(len(d) == fgnetfdm.FDM_SIZE == 408 for d in data)


def test_reset_publishes_while_frozen(udp_receiver):
    """B6 RESET: clean slate; one telemetry and one visual frame while frozen."""
    rx = udp_receiver(ports.NATIVE_FDM_PORT)
    p = ThreadPhysics()
    p.send("run"); p.send("engine", True); p.send("park_brake", False)
    p.send("autopilot", dict(ap=True)); p.send("fuel_freeze", True)
    steps(p, 30)
    p.send("pause"); p.step()
    rx.collect(0.1)
    p.send("reset"); p.step()
    got = rx.collect(0.2)
    t = p.latest()
    p.stop()
    assert len(got) == 1
    assert t["engine_state"] == "OFF" and t["park_brake"] and not t["ap_engaged"] and not t["fuel_freeze"]
    assert t["sim_time_s"] == 0.0 and not t["running"]


def test_freeze_gated_commands(phys):
    """fsim_TA_PCS_ThreadFlightSim_UIM.sysml caveats: reset/set position/snapshots/airport only in freeze."""
    phys.send("run")
    phys.send("select_airport", "EDDN")
    steps(phys, 0.1)
    assert phys.latest()["airport"] == "EDDM"
    phys.send("pause"); phys.send("select_airport", "EDDN"); phys.step()
    assert phys.latest()["airport"] == "EDDN"


def _running_on_ground():
    p = ThreadPhysics(send_fdm=False)
    started(p)
    p.send("park_brake", False)
    return p


def test_throttle_mapping_mil_detent():
    """L2SYS-REQ-01.02b / M13 (field defect D-1): MIL power at the 0.85 detent, afterburner above,
    low throttle gives low thrust."""
    p = _running_on_ground()
    p.send("park_brake", True)
    thrust = {}
    for th in (0.3, 0.85, 1.0):
        p.send("controls", dict(throttle=th))
        steps(p, 8)
        thrust[th] = (p.fdm["propulsion/engine/thrust-lbs"], p.latest()["n2_pct"])
    p.stop()
    assert thrust[0.3][0] < 4000
    assert 99.0 <= thrust[0.85][1] and 15000 < thrust[0.85][0] < 18000   # MIL, no afterburner
    assert thrust[1.0][0] > 25000                                         # full afterburner


def test_taxi_acceleration_realistic():
    """L2SYS-REQ-01.07 / field defect D-1: after start the aircraft creeps at idle and accelerates
    moderately at low throttle (previously 30 % throttle gave 0.34 g)."""
    p = _running_on_ground()
    assert steps(p, 10)["groundspeed_mps"] < 3.0            # idle creep < 0.3 m/s^2
    p.send("controls", dict(throttle=0.3))
    assert steps(p, 5)["groundspeed_mps"] < 10.0            # < ~1.6 m/s^2
    p.stop()


def test_rpm_returns_to_idle_and_brakes_stop_running_engine():
    """L2SYS-REQ-01.07 / field defects D-2, D-4: RPM follows the throttle down to idle (not
    below: idle N2 is the running floor), and wheel brakes stop the aircraft with the engine running."""
    p = _running_on_ground()
    p.send("controls", dict(throttle=0.85))
    t = steps(p, 5)
    assert t["n2_pct"] > 99 and t["groundspeed_mps"] > 20
    p.send("controls", dict(throttle=0.0))
    t = steps(p, 10)
    assert t["n2_pct"] < 56 and p.fdm["propulsion/engine/thrust-lbs"] < 1200
    p.send("controls", dict(throttle=0.0, wheel_brake=True))
    t = steps(p, 12)
    assert t["groundspeed_mps"] < 0.5 and t["engine_state"] == "RUNNING"
    p.stop()
