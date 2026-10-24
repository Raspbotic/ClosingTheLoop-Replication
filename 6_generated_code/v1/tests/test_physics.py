"""
tests/test_physics.py
=====================
Verification-plan rows allocated to the physics thread and the airport
table: L2SYS-REQ-01.02, 01.02b, 01.04, 01.07 plus the D6-D9 and D13
conformance rows.

[a: D15] every ThreadPhysics instance is stopped in teardown; the module
scoped ground-start fixture exists because JSBSim initialisation is slow.
"""

from __future__ import annotations

import json
import math
import time

import pytest

from flightsim.common.messages import HotasControls, OwnshipState
from flightsim.common import ports
from flightsim.pcs import airports as ap_mod
from flightsim.pcs.airports import AirportTable
from flightsim.pcs.physics import (
    BTN_AIRBRAKE_TOGGLE, BTN_GEAR_TOGGLE, BTN_WHEEL_BRAKE,
    FGNETFDM_FORMAT, FGNETFDM_SIZE, FGNETFDM_VERSION, IDLE_GATE_THROTTLE,
    TERRAIN_SLEW_FT_PER_FRAME, ThreadPhysics, unpack_fgnetfdm_header,
)

FT_PER_M = 1.0 / 0.3048


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ground():
    """Module-scoped ground-start Virtual Ownship (JSBSim init is slow)."""
    p = ThreadPhysics(send_to_flightgear=False)
    yield p
    p.stop()


@pytest.fixture
def fresh_ground(ground):
    """Reset the shared FDM to t0 before each test that dirties it."""
    ground.ssf_reset()
    ground.set_controls(HotasControls())
    return ground


@pytest.fixture
def own_physics():
    """A private instance for tests that need their own lifetime."""
    made = []

    def _make(**kwargs):
        kwargs.setdefault("send_to_flightgear", False)
        p = ThreadPhysics(**kwargs)
        made.append(p)
        return p

    yield _make
    for p in made:
        p.stop()


# ==========================================================================
# L2SYS-REQ-01.02 - the system shall comprise a Virtual Ownship
# ==========================================================================

def test_01_02_virtual_ownship_exists_and_produces_state(fresh_ground):
    """L2SYS-REQ-01.02: the system shall comprise a Virtual Ownship.

    Pass: a state carrying position, attitude and velocity is produced
    with finite values (the Virtual Ownship is realized by ThreadPhysics,
    see I-3 of docs/ARCHITECTURE.md).
    """
    s = fresh_ground.telemetry()
    assert isinstance(s, OwnshipState)
    for name in ("lat_deg", "lon_deg", "alt_m", "roll_deg", "pitch_deg",
                 "heading_deg", "speed_mps", "vspeed_mps"):
        assert math.isfinite(getattr(s, name)), name


# ==========================================================================
# L2SYS-REQ-01.02b - 120 Hz
# ==========================================================================

def test_01_02b_physics_step_advances_sim_time_by_one_120th(fresh_ground):
    """L2SYS-REQ-01.02b: compute the physical state at a 120 Hz frequency.

    Stimulus: 120 cyclic frames. Observation: the FDM simulation clock.
    Pass: each frame advances simulated time by exactly 1/120 s, so 120
    frames advance it by 1.000 s.
    """
    t0 = fresh_ground.telemetry().sim_time_s
    for _ in range(120):
        fresh_ground.step()
    t1 = fresh_ground.telemetry().sim_time_s
    assert (t1 - t0) == pytest.approx(1.0, abs=1e-9)
    assert fresh_ground.dt == pytest.approx(1.0 / 120.0)


def test_01_02b_control_inputs_change_the_computed_state(fresh_ground):
    """L2SYS-REQ-01.02b: the state is computed "based on received control
    inputs".

    Stimulus: full right aileron for 1 s with the engine running and the
    aircraft airborne. Pass: the roll attitude changes measurably.
    """
    fresh_ground.ssf_set_position(45.44, 9.28, 3000.0, speed_mps=200.0,
                                  heading_deg=90.0)
    roll0 = fresh_ground.telemetry().roll_deg
    fresh_ground.set_controls(HotasControls(roll=1.0, throttle=0.8))
    for _ in range(120):
        fresh_ground.step()
    roll1 = fresh_ground.telemetry().roll_deg
    assert abs(roll1 - roll0) > 5.0, "aileron input did not roll the aircraft"


@pytest.mark.slow
def test_01_02b_realtime_factor_within_tolerance(own_physics):
    """L2SYS-REQ-01.02b: the 120 Hz loop runs in real time.

    Stimulus: free-run the cyclic thread for 2 s of wall time.
    Pass criterion (plan): sim_time / wall_time in [0.85, 1.15].
    """
    p = own_physics()
    p.ssf_run()
    p.start()
    t_wall0 = time.monotonic()
    t_sim0 = p.telemetry().sim_time_s
    time.sleep(2.0)
    t_sim1 = p.telemetry().sim_time_s
    t_wall1 = time.monotonic()
    p.stop()
    rtf = (t_sim1 - t_sim0) / (t_wall1 - t_wall0)
    assert 0.85 <= rtf <= 1.15, f"real-time factor {rtf:.3f} out of band"


# ==========================================================================
# L2SYS-REQ-01.04 + D13 - ground start at a configurable airport
# ==========================================================================

@pytest.mark.parametrize("icao", ["LIML", "LIMC", "KSFO", "EDDF"])
def test_01_04_initializes_on_ground_at_selected_airport(own_physics, icao):
    """L2SYS-REQ-01.04: initialize the Virtual Ownship on ground at a
    user-selected airport from a configurable airport list.

    Observation: telemetry immediately after initialisation.
    Pass: position within 0.05 deg of the table entry, heading within 5 deg
    of the runway heading, weight on wheels, AGL below 15 m.
    """
    table = AirportTable(load_overrides=False)
    entry = table.get(icao)
    p = own_physics(icao=icao, table=table)
    s = p.telemetry()
    assert s.lat_deg == pytest.approx(entry.lat_deg, abs=0.05)
    assert s.lon_deg == pytest.approx(entry.lon_deg, abs=0.05)
    assert abs(((s.heading_deg - entry.runway_heading_deg + 180) % 360) - 180) < 5.0
    assert s.on_ground is True
    assert 0.0 <= s.agl_m < 15.0


def test_01_04_airport_list_is_configurable(tmp_path):
    """L2SYS-REQ-01.04 ("from a configurable airport list") and
    [a: D13]: the list is data-driven, not hard-wired into the FDM."""
    table = AirportTable(load_overrides=False)
    assert len(table) >= 4
    assert set(table.icaos()) >= {"LIML", "LIMC", "KSFO", "EDDF"}


def test_d13_seed_table_fields(tmp_path):
    """[a: D13] "Seed table LIML, LIMC, KSFO, EDDF (ICAO, name, lat/lon,
    runway heading, elevation)"."""
    table = AirportTable(load_overrides=False)
    assert table.icaos()[:4] == ["LIML", "LIMC", "KSFO", "EDDF"]
    for a in table.all():
        assert a.name and isinstance(a.name, str)
        assert -90.0 <= a.lat_deg <= 90.0
        assert -180.0 <= a.lon_deg <= 180.0
        assert 0.0 <= a.runway_heading_deg < 360.0
        assert math.isfinite(a.elevation_m)


def test_d13_local_override_is_partial_and_per_icao(tmp_path):
    """[a: D13] "optional airports_local.json per-ICAO partial overrides"."""
    path = tmp_path / ap_mod.LOCAL_OVERRIDE_FILENAME
    path.write_text(json.dumps({
        "LIML": {"elevation_m": 120.0},              # partial: one field
        "EDDM": {"name": "Munich", "lat_deg": 48.3538, "lon_deg": 11.7861,
                 "runway_heading_deg": 264.0, "elevation_m": 448.0},
    }))
    table = AirportTable(override_path=str(path))
    liml = table.get("LIML")
    assert liml.elevation_m == 120.0                 # overridden
    assert liml.name == "Milano Linate"              # untouched
    assert liml.lat_deg == pytest.approx(45.4451)    # untouched
    assert "EDDM" in table and table.get("EDDM").name == "Munich"
    assert table.override_error is None


def test_d13_corrupt_override_file_is_survivable(tmp_path):
    """[a: D13] "corrupt file survivable" - the seed table stays usable
    and the failure is reported rather than raised."""
    path = tmp_path / ap_mod.LOCAL_OVERRIDE_FILENAME
    path.write_text("{ this is not valid json ,,, ")
    table = AirportTable(override_path=str(path))
    assert table.override_error is not None
    assert set(table.icaos()) == {"LIML", "LIMC", "KSFO", "EDDF"}
    assert table.get("KSFO").name == "San Francisco Intl"


def test_d13_override_with_unknown_field_is_survivable(tmp_path):
    """[a: D13] a semantically corrupt file is as survivable as a
    syntactically corrupt one."""
    path = tmp_path / ap_mod.LOCAL_OVERRIDE_FILENAME
    path.write_text(json.dumps({"LIML": {"altitude": 999}}))
    table = AirportTable(override_path=str(path))
    assert table.override_error is not None
    assert table.get("LIML").elevation_m == 108.0


# ==========================================================================
# L2SYS-REQ-01.07 + D8 - engine and fuel semantics
# ==========================================================================

def test_01_07_engine_initializes_off_with_zero_fuel_consumption(fresh_ground):
    """L2SYS-REQ-01.07: "The Virtual Ownship engine shall initialize OFF
    with zero fuel consumption."

    Stimulus: 1 s of cyclic execution straight after initialisation.
    Pass: engine not running and the tank contents are invariant.
    """
    s0 = fresh_ground.telemetry()
    assert s0.engine_running is False
    assert s0.rpm_pct < 1.0
    for _ in range(120):
        fresh_ground.step()
    s1 = fresh_ground.telemetry()
    assert s1.fuel_kg == pytest.approx(s0.fuel_kg, abs=1e-9), \
        "fuel consumed with the engine OFF"


@pytest.mark.slow
def test_01_07_start_at_idle_spools_and_burns_fuel(fresh_ground):
    """L2SYS-REQ-01.07: "Upon engine start command (ENGINE set to ON) with
    throttle at idle, the engine shall spool to idle and fuel consumption
    shall commence."

    Stimulus: throttle at idle, ENGINE ON, 5 s of cyclic execution.
    Pass: RPM rises above zero and the tank contents strictly decrease.
    """
    fresh_ground.set_controls(HotasControls(throttle=0.0))
    fuel0 = fresh_ground.telemetry().fuel_kg
    assert fresh_ground.set_engine(True) is True
    for _ in range(600):
        fresh_ground.step()
    s = fresh_ground.telemetry()
    assert s.rpm_pct > 20.0, "engine did not spool to idle"
    assert s.engine_running is True
    assert s.fuel_kg < fuel0, "fuel consumption did not commence"


def test_d8_ground_start_state_is_engine_off_prkbrk_on_gear_down(fresh_ground):
    """[a: D8] "spawn on runway with engine OFF ..., PRK BRK ON, gear
    down". Also L2SYS-REQ-01.06c/01.06e initial states."""
    assert fresh_ground.engine_on is False
    assert fresh_ground.park_brake is True
    assert fresh_ground.gear_down is True
    assert fresh_ground.air_brake is False
    assert fresh_ground.telemetry().on_ground is True


def test_d8_engine_start_refused_above_the_idle_gate(fresh_ground):
    """[a: D8] "engine start refused above 10% throttle (idle gate)"."""
    fresh_ground.set_controls(HotasControls(throttle=IDLE_GATE_THROTTLE + 0.05))
    assert fresh_ground.set_engine(True) is False
    assert fresh_ground.engine_on is False
    fresh_ground.set_controls(HotasControls(throttle=IDLE_GATE_THROTTLE - 0.05))
    assert fresh_ground.set_engine(True) is True
    assert fresh_ground.engine_on is True


def test_d8_set_position_airborne_implies_engine_on_and_brake_released(
        fresh_ground):
    """[a: D8] "SSF set-position airborne implies engine ON and parking
    brake released". Also L2SYS-REQ-01.10b SET POSITION."""
    assert fresh_ground.park_brake is True
    s = fresh_ground.ssf_set_position(45.5, 9.3, 4000.0, speed_mps=180.0,
                                      heading_deg=45.0)
    assert fresh_ground.engine_on is True
    assert fresh_ground.park_brake is False
    assert s.alt_m == pytest.approx(4000.0, rel=0.02)
    assert s.on_ground is False


def test_d8_reset_is_a_clean_slate_and_publishes_while_frozen(own_physics):
    """[a: D8] "reset = clean slate (AP/fuel-freeze disengaged) and
    publishes one telemetry + one FG frame even while flight-frozen".
    Also L2SYS-REQ-01.10b RESET SIM."""
    published = []
    p = own_physics(telemetry_sink=published.append)
    # dirty everything
    p.set_controls(HotasControls(throttle=0.0))
    p.set_engine(True)
    p.set_park_brake(False)
    p.set_gear_down(False)
    p.set_air_brake(True)
    p.set_fuel_freeze(True)
    for _ in range(60):
        p.step()
    published.clear()
    frames_before = p.fg_frames_sent

    assert p.paused is True, "reset is exercised while flight-frozen"
    p.ssf_reset()

    assert p.engine_on is False
    assert p.park_brake is True
    assert p.gear_down is True
    assert p.air_brake is False
    assert p.fuel_freeze is False
    assert len(published) == 1, "reset must publish exactly one telemetry"
    assert p.fg_frames_sent == frames_before + 1, \
        "reset must publish exactly one FG frame"


def test_01_10b_fuel_freeze_holds_tank_contents(fresh_ground):
    """L2SYS-REQ-01.10b FUEL FREEZE ("hold tank contents constant");
    [a] TA action ssfFreezeFuelConsupmtion."""
    fresh_ground.set_controls(HotasControls(throttle=0.0))
    fresh_ground.set_engine(True)
    for _ in range(120):
        fresh_ground.step()
    fresh_ground.set_fuel_freeze(True)
    fuel0 = fresh_ground.telemetry().fuel_kg
    fresh_ground.set_controls(HotasControls(throttle=1.0))
    for _ in range(240):
        fresh_ground.step()
    assert fresh_ground.telemetry().fuel_kg == pytest.approx(fuel0, abs=1e-9)
    fresh_ground.set_fuel_freeze(False)
    for _ in range(240):
        fresh_ground.step()
    assert fresh_ground.telemetry().fuel_kg < fuel0, \
        "fuel burn did not resume after unfreezing"


# ==========================================================================
# D7 - control mapping
# ==========================================================================

def test_d7_yaw_drives_both_rudder_and_nose_wheel_steering(fresh_ground):
    """[a: D7] "yaw drives BOTH fcs/rudder-cmd-norm and fcs/steer-cmd-norm
    (nose-wheel steering)"."""
    fresh_ground.set_controls(HotasControls(yaw=0.62))
    fresh_ground.step()
    fdm = fresh_ground._fdm
    assert fdm["fcs/rudder-cmd-norm"] == pytest.approx(0.62, abs=1e-6)
    assert fdm["fcs/steer-cmd-norm"] == pytest.approx(0.62, abs=1e-6)


def test_d7_button_map_gear_airbrake_wheelbrake(fresh_ground):
    """[a: D7] "btn2 = landing-gear toggle; btn4 = air-brake toggle;
    btn6 = momentary wheel brake". Realizes 01.06d/01.06e from the HOTAS."""
    assert fresh_ground.gear_down is True
    assert fresh_ground.air_brake is False

    def press(idx, down=True):
        fresh_ground.set_controls(HotasControls(switches={f"btn{idx}": down}))

    press(BTN_GEAR_TOGGLE, True)
    assert fresh_ground.gear_down is False           # toggled on the edge
    press(BTN_GEAR_TOGGLE, False)
    assert fresh_ground.gear_down is False           # release does nothing
    press(BTN_GEAR_TOGGLE, True)
    assert fresh_ground.gear_down is True            # toggled back

    press(BTN_AIRBRAKE_TOGGLE, True)
    assert fresh_ground.air_brake is True
    press(BTN_AIRBRAKE_TOGGLE, False)
    assert fresh_ground.air_brake is True

    press(BTN_WHEEL_BRAKE, True)
    assert fresh_ground.wheel_brake is True          # momentary: follows state
    press(BTN_WHEEL_BRAKE, False)
    assert fresh_ground.wheel_brake is False


def test_01_06_system_control_commands_reach_the_fdm(fresh_ground):
    """L2SYS-REQ-01.06b/c/d/e at the Virtual Ownship end: each commanded
    function changes the corresponding FDM command."""
    fdm = fresh_ground._fdm
    fresh_ground.set_gear_down(False)
    assert fdm["gear/gear-cmd-norm"] == pytest.approx(0.0)
    fresh_ground.set_gear_down(True)
    assert fdm["gear/gear-cmd-norm"] == pytest.approx(1.0)

    fresh_ground.set_air_brake(True)
    assert fdm["fcs/speedbrake-cmd-norm"] == pytest.approx(1.0)
    fresh_ground.set_air_brake(False)
    assert fdm["fcs/speedbrake-cmd-norm"] == pytest.approx(0.0)

    fresh_ground.set_park_brake(False)
    assert fdm["fcs/left-brake-cmd-norm"] == pytest.approx(0.0)
    fresh_ground.set_park_brake(True)
    assert fdm["fcs/left-brake-cmd-norm"] == pytest.approx(1.0)
    assert fdm["fcs/right-brake-cmd-norm"] == pytest.approx(1.0)

    fresh_ground.set_engine(True)
    assert fdm["propulsion/cutoff_cmd"] == pytest.approx(0.0)
    fresh_ground.set_engine(False)
    assert fdm["propulsion/cutoff_cmd"] == pytest.approx(1.0)


# ==========================================================================
# D6 / D4 - FGNetFDM visual telemetry
# ==========================================================================

def test_d4_fgnetfdm_packet_is_408_bytes_version_24_bigendian(fresh_ground):
    """[a: D4] "FGNetFDM version 24, 408 bytes, big-endian"."""
    assert FGNETFDM_SIZE == 408
    assert FGNETFDM_FORMAT.startswith(">")           # big-endian
    frame = fresh_ground.build_fg_frame(fresh_ground.telemetry())
    assert len(frame) == 408
    version, lon_rad, lat_rad, alt_m = unpack_fgnetfdm_header(frame)
    assert version == FGNETFDM_VERSION == 24
    # position must survive the deg->rad conversion the packet requires
    s = fresh_ground.telemetry()
    assert math.degrees(lat_rad) == pytest.approx(s.lat_deg, abs=1e-6)
    assert math.degrees(lon_rad) == pytest.approx(s.lon_deg, abs=1e-6)


def test_d4_fdm_stream_is_decimated_to_60hz(fresh_ground):
    """[a: D4] "... decimated to ~60 Hz" from the 120 Hz physics rate."""
    before = fresh_ground.fg_frames_sent
    for _ in range(120):
        fresh_ground.step()
    sent = fresh_ground.fg_frames_sent - before
    assert sent == 60, f"expected 60 FG frames per 120 physics frames, got {sent}"


def test_d4_fdm_frames_reach_the_real_port(udp_receiver, own_physics):
    """[a: D4] the visual telemetry actually leaves on 127.0.0.1:5550."""
    rx = udp_receiver(ports.UDP_FG_NATIVE_FDM)
    p = own_physics(send_to_flightgear=True)
    for _ in range(6):
        p.step()
    assert rx.wait_for(1, timeout=2.0), "no FGNetFDM frame on port 5550"
    assert len(rx.datagrams[0]) == 408
    assert unpack_fgnetfdm_header(rx.datagrams[0])[0] == 24


def test_d6_jsbsim_model_is_f16(fresh_ground):
    """[a: D6] "JSBSim model f16"."""
    assert fresh_ground.aircraft == "f16"


# ==========================================================================
# D9 - terrain feedback loop
# ==========================================================================

def test_d9_terrain_elevation_slews_at_most_one_foot_per_frame(fresh_ground):
    """[a: D9] "feed into JSBSim position/terrain-elevation-asl-ft slewed
    <= 1 ft per 120 Hz frame"."""
    fdm = fresh_ground._fdm
    start_ft = fdm["position/terrain-elevation-asl-ft"]
    # a 100 m step, far larger than the per-frame limit
    fresh_ground.set_terrain_elevation_m(
        start_ft * 0.3048 + 100.0)
    prev = start_ft
    for _ in range(20):
        fresh_ground.step()
        now = fdm["position/terrain-elevation-asl-ft"]
        # 1e-6 ft tolerance absorbs the double<->property round-trip of
        # the FDM property tree; the physical limit is 1 ft per frame.
        assert abs(now - prev) <= TERRAIN_SLEW_FT_PER_FRAME + 1e-6, \
            "terrain moved more than 1 ft in one frame"
        prev = now
    assert prev > start_ft, "terrain did not slew towards the target"


def test_d9_terrain_reaches_the_target_and_stops(fresh_ground):
    """[a: D9] the slew converges on the commanded value and holds."""
    fdm = fresh_ground._fdm
    start_ft = fdm["position/terrain-elevation-asl-ft"]
    target_m = (start_ft + 3.0) * 0.3048
    fresh_ground.set_terrain_elevation_m(target_m)
    for _ in range(10):
        fresh_ground.step()
    assert fdm["position/terrain-elevation-asl-ft"] == pytest.approx(
        target_m * FT_PER_M, abs=1e-6)


def test_d9_poll_rate_constant_is_2hz():
    """[a: D9] "Poll FG ground elevation at 2 Hz"."""
    assert ports.TERRAIN_POLL_HZ == 2.0


# ==========================================================================
# Snapshot backend (01.10b SAVE/LOAD)
# ==========================================================================

def test_01_10b_snapshot_is_a_deep_copy_without_aliasing(fresh_ground):
    """L2SYS-REQ-01.10b SAVE SNAPSHOT / LOAD SNAPSHOT; [a] R-8 "no
    aliasing": mutating the returned snapshot must not touch the sim, and
    later frames must not mutate the snapshot."""
    fresh_ground.set_controls(HotasControls(throttle=0.0))
    fresh_ground.set_engine(True)
    snap = fresh_ground.snapshot_state()
    fuel_in_snapshot = list(snap["fuel_lbs"])        # type: ignore[arg-type]
    for _ in range(240):
        fresh_ground.step()
    assert snap["fuel_lbs"] == fuel_in_snapshot, "snapshot was aliased"
    snap["fuel_lbs"][0] = -1.0                       # type: ignore[index]
    assert fresh_ground._fdm["propulsion/tank[0]/contents-lbs"] > 0


def test_01_10b_snapshot_round_trip_restores_state(fresh_ground):
    """L2SYS-REQ-01.10b: "SAVE SNAPSHOT / LOAD SNAPSHOT: save/restore full
    simulation state"."""
    fresh_ground.ssf_set_position(46.0, 10.0, 2500.0, speed_mps=150.0,
                                  heading_deg=90.0)
    fresh_ground.set_air_brake(True)
    fresh_ground.set_gear_down(True)
    snap = fresh_ground.snapshot_state()
    saved = fresh_ground.telemetry()

    # dirty the simulation
    fresh_ground.ssf_reset()
    assert fresh_ground.telemetry().alt_m < 1000.0

    restored = fresh_ground.restore_state(snap)
    assert restored.lat_deg == pytest.approx(saved.lat_deg, abs=1e-4)
    assert restored.lon_deg == pytest.approx(saved.lon_deg, abs=1e-4)
    assert restored.alt_m == pytest.approx(saved.alt_m, rel=1e-3)
    assert fresh_ground.air_brake is True
    assert fresh_ground.engine_on is True
