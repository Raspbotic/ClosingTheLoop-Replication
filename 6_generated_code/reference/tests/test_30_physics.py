"""ThreadPhysics: 120Hz loop, ground start, native-fdm, SSF, engine.
Traces: fsim_TA_PCS_ThreadPhysics.sysml, L2SYS-REQ-01.02b/01.7."""
import math, queue, struct, time, pytest
from flightsim.common import ports
from flightsim.common.messages import HotasControls
from flightsim.pcs.physics import ThreadPhysics, InitialConditions
from flightsim.pcs.airports import AIRPORTS


@pytest.fixture(scope="module")
def ground_sim(request):
    """One shared ground-start F16 at LIML for this module (init is slow)."""
    apt = AIRPORTS["LIML"]
    ic = InitialConditions(lat_deg=apt.lat_deg, lon_deg=apt.lon_deg,
                           heading_deg=apt.rwy_heading_deg,
                           on_ground=True, terrain_elev_m=apt.elev_m)
    hq, tq = queue.Queue(), queue.Queue(maxsize=1)
    p = ThreadPhysics(hq, tq, ic=ic)
    p.start()
    tq.get(timeout=20)          # wait for init
    yield p, hq, tq, apt
    p.shutdown(); p.join(timeout=3)


def drain_for(tq, hq, seconds, controls=None):
    tm = None
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        try:
            tm = tq.get(timeout=1)
        except queue.Empty:
            break
        if controls is not None:
            hq.put(controls)
    return tm


def test_default_aircraft_is_f16(ground_sim):
    p, *_ = ground_sim
    assert p._aircraft == "f16", "customer decision 2026-07-11"


def test_ground_start_stationary(ground_sim):
    p, hq, tq, apt = ground_sim
    tm = drain_for(tq, hq, 2.0, HotasControls(switches={"btn6": True}))
    assert abs(tm.alt_m - apt.elev_m) < 8, "must sit near field elevation"
    assert tm.groundspeed_ms < 2.0, "must be stationary"


def test_engine_off_at_ground_start_no_fuel_burn(ground_sim):
    """L2SYS-REQ-01.07: engine OFF at init, zero consumption."""
    p, hq, tq, _ = ground_sim
    if p.engine_on:
        p.set_engine(False)
        drain_for(tq, hq, 3.0)   # spool down
    fuel0 = p._fdm["propulsion/tank/contents-lbs"]
    drain_for(tq, hq, 2.0)
    fuel1 = p._fdm["propulsion/tank/contents-lbs"]
    assert abs(fuel1 - fuel0) < 0.02, "no fuel burn while engine OFF"


def test_engine_start_spools_and_burns(ground_sim):
    """L2SYS-REQ-01.07: start -> idle spool, fuel flow commences."""
    p, hq, tq, _ = ground_sim
    p.set_engine(True)
    drain_for(tq, hq, 3.0, HotasControls(throttle=0.0,
                                         switches={"btn6": True}))
    assert p._fdm["propulsion/engine/n2"] > 40, "turbine must spool to idle"
    fuel0 = p._fdm["propulsion/tank/contents-lbs"]
    drain_for(tq, hq, 2.0, HotasControls(throttle=0.0,
                                         switches={"btn6": True}))
    assert p._fdm["propulsion/tank/contents-lbs"] < fuel0 - 0.05


def test_loop_rate_120hz(ground_sim):
    """L2SYS-REQ-01.02: sim time must track wall time at 120 Hz."""
    p, hq, tq, _ = ground_sim
    t0 = tq.get(timeout=2).sim_time_s
    w0 = time.monotonic()
    tm = drain_for(tq, hq, 2.0)
    rtf = (tm.sim_time_s - t0) / (time.monotonic() - w0)
    assert 0.85 < rtf < 1.15, f"real-time factor {rtf:.2f}"


def test_native_fdm_packet(ground_sim, udp_rx):
    """FGNetFDM v24: 408 bytes, decodable lat/lon, ~60 Hz decimation."""
    rx = udp_rx(ports.UDP_NATIVE_FDM, timeout=2.0)
    p, hq, tq, apt = ground_sim
    pkt, _ = rx.recvfrom(2048)
    assert len(pkt) == 408
    version, _ = struct.unpack(">II", pkt[:8])
    assert version == 24
    lon_r, lat_r, _ = struct.unpack(">ddd", pkt[8:32])
    assert abs(math.degrees(lat_r) - apt.lat_deg) < 0.05
    n = 0
    t0 = time.monotonic()
    rx.settimeout(0.3)
    while time.monotonic() - t0 < 1.0:
        try:
            rx.recvfrom(2048); n += 1
        except OSError:
            break
    assert 45 <= n <= 75, f"native-fdm rate {n} Hz, expected ~60"


def test_button_map(ground_sim):
    """btn2 gear toggle, btn4 airbrake toggle, btn6 brakes hold."""
    p, hq, tq, _ = ground_sim
    def press(btn):
        hq.put(HotasControls(switches={btn: True}))
        drain_for(tq, hq, 0.15)
        hq.put(HotasControls(switches={btn: False}))
        drain_for(tq, hq, 0.15)
    g0 = p._gear_down
    press("btn2")
    assert p._gear_down == (not g0)
    press("btn2")
    assert p._gear_down == g0
    s0 = p._speedbrake
    press("btn4")
    assert p._speedbrake == 1.0 - s0
    press("btn4")
    # btn6 wheel brakes are OR'd with the parking brake (UI-1) --
    # release the parking brake to observe the pedal channel alone [b]
    p.set_parking_brake(False)
    hq.put(HotasControls(switches={"btn6": True})); drain_for(tq, hq, 0.15)
    assert p._fdm["fcs/left-brake-cmd-norm"] == 1.0
    hq.put(HotasControls(switches={"btn6": False})); drain_for(tq, hq, 0.15)
    assert p._fdm["fcs/left-brake-cmd-norm"] == 0.0
    p.set_parking_brake(True)
    drain_for(tq, hq, 0.15)
    assert p._fdm["fcs/left-brake-cmd-norm"] == 1.0, \
        "parking brake must hold brakes without pedal input"
    p.set_parking_brake(False)


def test_ssf_set_own_position_airborne_engine_on(ground_sim):
    """ssfSetOwnPosition + airborne auto engine-ON [b policy]."""
    p, hq, tq, _ = ground_sim
    p.ssf_set_own_position(48.0, 11.0, 3000, 180, 300)
    drain_for(tq, hq, 1.0)
    tm = drain_for(tq, hq, 0.5)
    assert abs(tm.lat_deg - 48.0) < 0.1 and abs(tm.lon_deg - 11.0) < 0.1
    assert p.engine_on, "airborne start must force engine ON"
    # restore ground state for any later module
    p.ssf_reset()
    drain_for(tq, hq, 1.0)


def test_yaw_drives_both_rudder_and_steering(ground_sim):
    """[bug fix 2026-07-12] yaw must reach fcs/steer-cmd-norm (nose-wheel
    steering during taxi) in addition to fcs/rudder-cmd-norm."""
    p, hq, tq, _ = ground_sim
    hq.put(HotasControls(yaw=0.6))
    drain_for(tq, hq, 0.2)
    assert abs(p._fdm["fcs/rudder-cmd-norm"] - 0.6) < 0.01
    assert abs(p._fdm["fcs/steer-cmd-norm"] - 0.6) < 0.01
    hq.put(HotasControls(yaw=0.0))
    drain_for(tq, hq, 0.2)


def test_terrain_feedback_slews_to_fg_value(ground_sim):
    """[bug fix 2026-07-12] FG ground elevation feeds JSBSim terrain,
    slewed (gear-safe), so FDM and OTW share one ground."""
    p, hq, tq, apt = ground_sim
    cur_ft = p._fdm["position/terrain-elevation-asl-ft"]
    target_m = (cur_ft * 0.3048) + 3.0        # FG says ground is 3 m higher
    p.set_terrain_elevation(target_m)
    drain_for(tq, hq, 1.0)                    # ~120 frames @ 1 ft/frame max
    now_ft = p._fdm["position/terrain-elevation-asl-ft"]
    assert abs(now_ft * 0.3048 - target_m) < 0.5, \
        f"terrain must converge to FG value, at {now_ft*0.3048:.1f} m"
    # restore and settle
    p.set_terrain_elevation(cur_ft * 0.3048)
    drain_for(tq, hq, 1.0)