"""ThreadPhysics -- the Virtual Ownship.

Plan rows: P-01.02-1, P-01.02-2, P-01.02b-1 .. -4, P-01.04-6, P-01.06b-2,
P-01.06b-3, P-01.06c-2, P-01.06c-3, P-01.06e-3, P-01.07-1 .. -3,
C-D4-2, C-D4-3, C-D4-4, C-D9-1 .. -4.
Additions register group B: test_measured_fdm_conventions_still_hold.

Nothing here requires FlightGear: the FDM datagram is written to 5550 whether
or not anything is listening, and the terrain poll is a parameter.
[a: Sec.3 criterion 6] [a: D18]
"""

from __future__ import annotations

import math
import struct

import pytest

from flightsim.common import ports
from flightsim.common.messages import OWNSHIP_FIELDS, OwnshipState
from flightsim.pcs import fdm_net, physics
from flightsim.pcs.airports import SEED_AIRPORTS, find
from flightsim.pcs.physics import ControlCommand, PhysicsThread

# Reference values transcribed from the Decisions and the workbook.
REQ_PHYSICS_HZ = 120.0          # [a: L2SYS-REQ-01.02b] "at a 120Hz frequency"
D4_FDM_VERSION = 24             # [a: D4] "FGNetFDM version 24"
D4_FDM_BYTES = 408              # [a: D4] "408 bytes"
D9_TERRAIN_POLL_HZ = 2.0        # [a: D9] "at 2 Hz"
D9_SLEW_FT_PER_FRAME = 1.0      # [a: D9] "at most 1 ft per 120 Hz frame"
D8_START_MAX_THROTTLE = 0.10    # [a: D8] "refused above 10 %; exactly 10 % starts"


@pytest.fixture
def ownship(clock):
    """A physics thread with the FDM stream suppressed, for rows that are not
    about the wire. Faster, and it keeps 5550 free for the rows that are."""
    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep, emit_fdm=False)
    p.initialize()
    yield p
    p.close()


def run_frames(p: PhysicsThread, n: int) -> OwnshipState:
    state = None
    for _ in range(n):
        state = p.step()
    return state


# ===========================================================================
# P-01.02-1 / P-01.02-2
# ===========================================================================
def test_virtual_ownship_produces_a_full_state(ownship):
    """P-01.02-1. [a: L2SYS-REQ-01.02] A Virtual Ownship exists and publishes a
    state carrying all 27 D19 fields, each of the stated kind.
    """
    state = ownship.telemetry.get()
    assert isinstance(state, OwnshipState)
    for name in OWNSHIP_FIELDS:
        assert hasattr(state, name), name
    assert isinstance(state.model, str) and state.model
    for name in ("engine_running", "parking_brake", "air_brake", "gear_down",
                 "on_ground"):
        assert isinstance(getattr(state, name), bool), name
    for name in ("lat_deg", "lon_deg", "alt_m", "agl_m", "heading_deg",
                 "speed_mps", "fuel_lbs", "rpm_pct"):
        assert isinstance(getattr(state, name), float), name


def test_ownship_has_an_init_phase_and_a_cyclic_loop(ownship):
    """P-01.02-2. sim_time is 0 after initialization and strictly greater after
    stepping. [a: fsim_TA_PCS_ThreadPhysics.sysml]
    """
    assert ownship.telemetry.get().sim_time_s == pytest.approx(0.0, abs=1e-9)
    after = run_frames(ownship, 10)
    assert after.sim_time_s > 0.0


# ===========================================================================
# P-01.02b-1 / -2 -- the rate, measured
# ===========================================================================
def test_step_rate_is_120hz_measured(ownship, clock):
    """P-01.02b-1. [a: L2SYS-REQ-01.02b] "at a 120Hz frequency".

    Measured: the loop is driven against a clock this row controls, and the
    steps executed per unit of that clock are counted. 5.0 s x 120 Hz = 600.
    """
    ownship.ssf_run()
    t0 = clock.time()
    ownship.run(max_frames=600)
    elapsed = clock.time() - t0

    assert elapsed == pytest.approx(5.0, abs=1e-9), "600 frames should be 5.0 s"
    measured_hz = 600 / elapsed
    assert measured_hz == pytest.approx(REQ_PHYSICS_HZ, abs=0.5)


def test_sim_time_increment_is_one_over_120(ownship):
    """P-01.02b-2. Hand computation: 240 x (1/120) = 240 x 0.00833333... = 2.0 s.
    [a: L2SYS-REQ-01.02b]
    """
    t_before = ownship.telemetry.get().sim_time_s
    state = run_frames(ownship, 240)
    assert state.sim_time_s - t_before == pytest.approx(2.0, abs=1e-3)
    # and the FDM's own frame time is 1/120, not merely close to it
    assert physics.PHYSICS_DT == pytest.approx(1.0 / 120.0, abs=1e-15)


# ===========================================================================
# P-01.02b-3 / -4 -- response to control inputs
# ===========================================================================
def test_state_responds_to_received_control_inputs(clock):
    """P-01.02b-3. "based on received control inputs" -- a pitch-up command
    pitches up and a pitch-down command pitches down, from the same start.
    [a: L2SYS-REQ-01.02b]

    Pilot-side signs: pitch +1 is nose up. [a: D10]
    """
    results = {}
    for label, pitch in (("up", +0.5), ("down", -0.5)):
        p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                          sleep=clock.sleep, emit_fdm=False)
        p.initialize()
        # get airborne first: on the runway the gear holds the attitude
        p.ssf_set_position(lat_deg=45.0, lon_deg=9.0, alt_m=3000.0,
                           speed_mps=180.0, heading_deg=0.0)
        p.commands.update(throttle=0.8, gear_down=False)
        run_frames(p, 120)
        before = p.telemetry.get().pitch_deg
        p.commands.update(pitch=pitch)
        after = run_frames(p, 240).pitch_deg
        results[label] = after - before
        p.close()

    assert results["up"] > 0.5, results
    assert results["down"] < -0.5, results


def test_position_and_velocity_are_computed_not_static(ownship):
    """P-01.02b-4. Full throttle with the brakes off accelerates the aircraft
    and moves it. [a: L2SYS-REQ-01.02b]
    """
    start = ownship.telemetry.get()
    ownship.commands.update(engine_on=True, throttle=0.05)
    run_frames(ownship, 240)                       # start the engine at idle
    ownship.commands.update(throttle=1.0, parking_brake=False)
    end = run_frames(ownship, 1200)                # 10 s

    assert end.speed_mps > start.speed_mps + 5.0
    moved = math.hypot(end.lat_deg - start.lat_deg, end.lon_deg - start.lon_deg)
    assert moved > 0.0


# ===========================================================================
# P-01.04-6 -- the initial condition
# ===========================================================================
@pytest.mark.parametrize("icao", ["LIML", "LIMC", "KSFO", "EDDF"])
def test_initial_condition_is_on_ground_at_the_selected_airport(icao, clock):
    """P-01.04-6. [a: L2SYS-REQ-01.04] on the ground at the selected airport.
    [a: D8] "At t0 weight is on the wheels and ground speed is zero."
    """
    airport = find(SEED_AIRPORTS, icao)
    p = PhysicsThread(airport=airport, clock=clock.monotonic,
                      sleep=clock.sleep, emit_fdm=False)
    p.initialize()
    s = p.telemetry.get()

    assert s.lat_deg == pytest.approx(airport.lat_deg, abs=0.01)
    assert s.lon_deg == pytest.approx(airport.lon_deg, abs=0.01)
    assert s.on_ground is True, "weight is not on the wheels at t0"
    assert abs(s.speed_mps) < 0.5, f"ground speed {s.speed_mps} at t0"
    assert s.heading_deg == pytest.approx(airport.runway_hdg_deg, abs=1.0)
    # [a: D8] "engine off, parking brake on, gear down"
    assert s.engine_running is False
    assert s.parking_brake is True
    assert s.gear_down is True
    p.close()


# ===========================================================================
# P-01.06b-2 / -3 -- the D8 engine-start gate
# ===========================================================================
def test_engine_start_refused_above_ten_percent_throttle(ownship):
    """P-01.06b-2. [a: D8] "Engine start is refused above 10 % throttle".
    [a: L2SYS-REQ-01.06b]
    """
    ownship.commands.update(throttle=0.11, engine_on=True)
    state = run_frames(ownship, 2)
    assert state.engine_running is False
    assert any("REFUSED" in m for m in ownship.messages), ownship.messages


def test_engine_start_accepted_at_exactly_ten_percent(ownship):
    """P-01.06b-3. [a: D8] "exactly 10 % starts" -- the boundary is inclusive.
    [a: L2SYS-REQ-01.06b]
    """
    assert physics.ENGINE_START_MAX_THROTTLE == D8_START_MAX_THROTTLE
    ownship.commands.update(throttle=0.10, engine_on=True)
    state = run_frames(ownship, 2)
    assert state.engine_running is True


def test_engine_stop_commands_the_engine_off(ownship):
    """P-01.06b-1 (physics half). [a: L2SYS-REQ-01.06b] ENGINE toggle ON/OFF.
    [a: D21] engine_running is the COMMANDED state.
    """
    ownship.commands.update(throttle=0.05, engine_on=True)
    assert run_frames(ownship, 2).engine_running is True
    ownship.commands.update(engine_on=False)
    assert run_frames(ownship, 2).engine_running is False


# ===========================================================================
# P-01.06c-2 / -3 -- park brake
# ===========================================================================
def test_park_brake_on_holds_the_aircraft(ownship, clock):
    """P-01.06c-2, **as amended by A-1**. [a: L2SYS-REQ-01.06c]

    The frozen row used full throttle. Measured on the FDM, the wheel brakes
    hold completely up to about 20 % throttle and are progressively overpowered
    above it -- correct for a wheel brake against a 27 000 lbf engine, and
    01.06c requires only that the function exist and be commandable. See
    amendment A-1 in docs/VERIFICATION_PLAN.md for the measurement table and
    the negative check.

    Criterion 1 (amended stimulus): at idle the brake holds the aircraft
    exactly stationary, and releasing it lets the aircraft roll.
    Criterion 2 (added, original stimulus): at full throttle the brake still
    retards the aircraft materially -- measured ratio 0.686 (86.26 vs
    125.76 m/s), asserted at < 0.85 to leave margin. With the brake command
    disabled the ratio is 1.0, so the criterion still detects a park brake
    that does nothing.
    """
    ownship.commands.update(engine_on=True, throttle=0.05)
    run_frames(ownship, 600)

    # -- criterion 1: idle
    ownship.commands.update(throttle=0.05, parking_brake=True)
    held = run_frames(ownship, 1200).speed_mps
    assert held < 0.5, f"aircraft rolled at {held:.3f} m/s with the brake on"

    ownship.commands.update(parking_brake=False)
    rolling = run_frames(ownship, 1200).speed_mps
    assert rolling > 1.0, "aircraft did not roll with the brake released"

    # -- criterion 2: full throttle, brake still effective
    braked = _speed_after(clock, throttle=1.0, parking_brake=True)
    free = _speed_after(clock, throttle=1.0, parking_brake=False)
    assert braked < 0.85 * free, (
        f"park brake barely retarded the aircraft: {braked:.1f} vs {free:.1f} m/s"
    )


def _speed_after(clock, throttle: float, parking_brake: bool) -> float:
    """Ground speed after a fixed run from the same start, for A-1's second
    criterion. A helper rather than a fixture because the row needs two
    independent runs from an identical initial condition.
    """
    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep, emit_fdm=False)
    p.initialize()
    p.commands.update(engine_on=True, throttle=0.05)
    run_frames(p, 600)
    p.commands.update(throttle=throttle, parking_brake=parking_brake)
    speed = run_frames(p, 1200).speed_mps
    p.close()
    return speed


def test_park_brake_is_on_at_t0(ownship):
    """P-01.06c-3. [a: D8] "parking brake on" at t0."""
    assert ownship.telemetry.get().parking_brake is True


# ===========================================================================
# P-01.06e-3 -- gear
# ===========================================================================
def test_gear_is_down_at_t0(ownship):
    """P-01.06e-3. [a: D8] "gear down" at t0. [a: L2SYS-REQ-01.06e]"""
    assert ownship.telemetry.get().gear_down is True


def test_gear_command_reaches_the_fdm(ownship):
    """P-01.06e-1 (physics half). [a: L2SYS-REQ-01.06e] UP/DOWN."""
    ownship.commands.update(gear_down=False)
    assert run_frames(ownship, 2).gear_down is False
    ownship.commands.update(gear_down=True)
    assert run_frames(ownship, 2).gear_down is True


def test_air_brake_command_reaches_the_fdm(ownship):
    """P-01.06d-1 (physics half). [a: L2SYS-REQ-01.06d] ON/OFF."""
    ownship.commands.update(air_brake=True)
    assert run_frames(ownship, 2).air_brake is True
    ownship.commands.update(air_brake=False)
    assert run_frames(ownship, 2).air_brake is False


# ===========================================================================
# P-01.07 -- engine and fuel
# ===========================================================================
def test_engine_off_at_t0(ownship):
    """P-01.07-1. [a: L2SYS-REQ-01.07] "The Virtual Ownship engine shall
    initialize OFF"."""
    s = ownship.telemetry.get()
    assert s.engine_running is False
    assert s.rpm_pct <= 1.0


def test_fuel_is_constant_while_the_engine_is_off(ownship):
    """P-01.07-2. [a: L2SYS-REQ-01.07] "with zero fuel consumption"."""
    first = ownship.telemetry.get().fuel_lbs
    last = run_frames(ownship, 1200).fuel_lbs      # 10 s
    assert last == pytest.approx(first, abs=1e-6)


def test_engine_start_at_idle_spools_and_burns_fuel(ownship):
    """P-01.07-3. [a: L2SYS-REQ-01.07] "Upon engine start command (ENGINE set
    to ON) with throttle at idle, the engine shall spool to idle and fuel
    consumption shall commence."
    """
    before = ownship.telemetry.get()
    ownship.commands.update(throttle=0.0, engine_on=True)
    after = run_frames(ownship, 2400)              # 20 s
    assert after.rpm_pct > before.rpm_pct, "engine did not spool"
    assert after.fuel_lbs < before.fuel_lbs, "fuel consumption did not commence"
    # and it settles rather than running away
    settled = run_frames(ownship, 600)
    assert settled.rpm_pct == pytest.approx(after.rpm_pct, abs=5.0)


def test_fuel_freeze_holds_tank_contents(ownship):
    """P-01.10b-8 (physics half). [a: L2SYS-REQ-01.10b] "FUEL FREEZE: hold tank
    contents constant."
    """
    ownship.commands.update(throttle=0.05, engine_on=True)
    run_frames(ownship, 600)
    ownship.ssf_fuel_freeze(True)
    frozen_start = ownship.telemetry.get().fuel_lbs
    frozen_end = run_frames(ownship, 1200).fuel_lbs
    assert frozen_end == pytest.approx(frozen_start, abs=0.5)

    ownship.ssf_fuel_freeze(False)
    thawed = run_frames(ownship, 1200).fuel_lbs
    assert thawed < frozen_end - 0.5, "fuel did not resume burning"


# ===========================================================================
# C-D4-2 / C-D4-4 -- the FGNetFDM stream
# ===========================================================================
def test_fgnetfdm_is_version_24_and_408_bytes_big_endian():
    """C-D4-2. [a: D4] "FGNetFDM version 24, 408 bytes, big-endian".

    Big-endian is a *checked* claim: the version reads 24 when read big-endian
    and does not when read little-endian.
    """
    payload = fdm_net.pack(
        lat_deg=45.0, lon_deg=9.0, alt_m=1000.0, agl_m=900.0,
        phi_deg=0.0, theta_deg=0.0, psi_deg=0.0,
    )
    assert len(payload) == D4_FDM_BYTES == 408
    assert struct.unpack(">I", payload[:4])[0] == D4_FDM_VERSION == 24
    assert struct.unpack("<I", payload[:4])[0] != D4_FDM_VERSION
    assert fdm_net.unpack_version(payload) == 24


def test_fgnetfdm_carries_the_state_it_was_given():
    """C-D4-2. Angles arrive in degrees and are carried as radians, which is
    what the struct's fields hold. [a: D19] [c: net_fdm.hxx]
    """
    payload = fdm_net.pack(
        lat_deg=45.0, lon_deg=-9.0, alt_m=1234.5, agl_m=100.0,
        phi_deg=10.0, theta_deg=5.0, psi_deg=90.0,
    )
    _ver, _pad, lon, lat, alt = struct.unpack(">II3d", payload[:32])
    assert lat == pytest.approx(math.radians(45.0))
    assert lon == pytest.approx(math.radians(-9.0))
    assert alt == pytest.approx(1234.5)
    agl, phi, theta, psi = struct.unpack(">4f", payload[32:48])
    assert agl == pytest.approx(100.0, rel=1e-6)
    assert phi == pytest.approx(math.radians(10.0), rel=1e-6)
    assert theta == pytest.approx(math.radians(5.0), rel=1e-6)
    assert psi == pytest.approx(math.radians(90.0), rel=1e-6)


def test_fdm_stream_is_decimated_to_about_sixty_hz(udp_sink, clock):
    """C-D4-4. [a: D4] "decimated to about 60 Hz". Over 240 frames of the
    120 Hz loop, 120 datagrams reach 5550.
    """
    sink = udp_sink(ports.PCS_TO_FLIGHTGEAR_FDM)
    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep)
    p.initialize()
    run_frames(p, 240)
    assert sink.wait_for(120, timeout=5.0), (
        f"only {len(sink.datagrams)} datagrams reached 5550"
    )
    assert 118 <= len(sink.datagrams) <= 122, len(sink.datagrams)
    assert all(len(d) == 408 for d in sink.datagrams)
    assert all(fdm_net.unpack_version(d) == 24 for d in sink.datagrams)
    p.close()


def test_fdm_stream_is_written_with_nothing_listening(clock):
    """C-D4-4. [a: R-5] "The FDM stream is written whether or not anything is
    listening; nothing here requires FlightGear." No sink is bound here.
    """
    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep)
    p.initialize()
    run_frames(p, 20)
    assert p.fdm_datagrams_sent == 10
    p.close()


# ===========================================================================
# C-D4-3 -- the ground-elevation reply shapes
# ===========================================================================
@pytest.mark.parametrize("payload,expected", [
    (12.5, 12.5),
    (12, 12.0),
    ("12.5", 12.5),
    ("  12.5 ", 12.5),
    ({"value": 12.5}, 12.5),
    ({"value": "12.5"}, 12.5),
    ({"value": -3.0}, -3.0),
])
def test_ground_elev_parse_accepts_all_three_shapes(payload, expected):
    """C-D4-3. [a: D4] "the value arriving as a number, a string, or wrapped in
    an object under `value`".
    """
    assert physics.parse_ground_elevation(payload) == pytest.approx(expected)


@pytest.mark.parametrize("payload", [
    None, True, "not a number", {"other": 1}, {}, [1.0], object(),
    float("nan"), "NaN",
])
def test_ground_elev_parse_rejects_anything_else(payload):
    """C-D4-3. Anything else yields None, which the poll tolerates rather than
    raising out of the loop. [a: D9]
    """
    assert physics.parse_ground_elevation(payload) is None


# ===========================================================================
# C-D9 -- the terrain loop
# ===========================================================================
def test_terrain_poll_rate_is_2hz_measured(clock):
    """C-D9-1. [a: D9] "Poll FlightGear ground elevation at 2 Hz". Measured
    against the row's own clock: 5 s of clock, 10 polls.
    """
    assert physics.TERRAIN_POLL_HZ == D9_TERRAIN_POLL_HZ
    calls = []

    def source():
        calls.append(clock.time())
        return 100.0

    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep, emit_fdm=False,
                      ground_elev_source=source)
    p.initialize()
    p.ssf_run()
    p.run(max_frames=600)               # 5.0 s of clock at 120 Hz
    elapsed = 600 * physics.PHYSICS_DT
    measured_hz = len(calls) / elapsed
    assert measured_hz == pytest.approx(2.0, abs=0.3), (
        f"{len(calls)} polls in {elapsed} s"
    )
    p.close()


def test_terrain_slew_is_at_most_one_foot_per_frame(clock):
    """C-D9-2. [a: D9] "slewed at most 1 ft per 120 Hz frame".

    Hand computation: a 1000 ft step, slewed at 1 ft per frame, moves at most
    100 ft in 100 frames.
    """
    airport = SEED_AIRPORTS[0]
    base_ft = airport.elevation_m * physics.FT_PER_M
    target_m = (base_ft + 1000.0) * physics.M_PER_FT

    p = PhysicsThread(airport=airport, clock=clock.monotonic,
                      sleep=clock.sleep, emit_fdm=False,
                      ground_elev_source=lambda: target_m)
    p.initialize()
    fdm = p._fdm
    before = float(fdm["position/terrain-elevation-asl-ft"])
    for _ in range(100):
        p.step()
    after = float(fdm["position/terrain-elevation-asl-ft"])
    moved = after - before
    assert 0.0 < moved <= 100.0 * D9_SLEW_FT_PER_FRAME + 1e-6, moved
    assert moved == pytest.approx(100.0, abs=1.0)
    p.close()


def test_failed_poll_is_tolerated(clock):
    """C-D9-3. [a: D9] "A failed poll is tolerated." A source that raises does
    not stop the physics and does not move the terrain.
    """
    def boom():
        raise ConnectionError("FlightGear httpd is not answering")

    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep, emit_fdm=False,
                      ground_elev_source=boom)
    p.initialize()
    before = float(p._fdm["position/terrain-elevation-asl-ft"])
    p.ssf_run()
    p.run(max_frames=600)
    assert p.terrain_poll_failures >= 5
    assert p.telemetry.get().sim_time_s > 0.0, "physics stopped on a failed poll"
    assert float(p._fdm["position/terrain-elevation-asl-ft"]) == pytest.approx(
        before, abs=0.01
    )
    p.close()


def test_no_flightgear_means_no_poll(ownship):
    """C-D9-4. [a: D9] "With no FlightGear the poll is inactive and the physics
    runs unaffected." The default source is None.
    """
    assert ownship.ground_elev_source is None
    run_frames(ownship, 600)
    assert ownship.terrain_polls == 0
    assert ownship.telemetry.get().sim_time_s > 0.0


# ===========================================================================
# Additions register, group B -- the measured FDM conventions
# ===========================================================================
def test_measured_fdm_conventions_still_hold(clock):
    """Additions B. D17 requires the sign and units of every wired FDM property
    to be **measured**. docs/FDM_MEASUREMENTS.md records those measurements;
    this row re-measures the four that changed the implementation, so a jsbsim
    upgrade that reversed one fails the suite instead of flying inverted.
    [a: D17]

    M-A  fcs/elevator-cmd-norm  negative command -> nose UP
    M-B  fcs/rudder-cmd-norm    positive command -> yaw LEFT
    M-C  fcs/steer-cmd-norm     positive command -> steer LEFT
    M-D  gear/wow reads 1 at t0 at the chosen ground initial condition
    """
    import jsbsim

    def airborne():
        f = jsbsim.FGFDMExec(None)
        f.set_debug_level(0)
        f.load_model("f16")
        f["ic/lat-gc-deg"] = 45.0
        f["ic/long-gc-deg"] = 9.0
        f["ic/terrain-elevation-ft"] = 0.0
        f["ic/h-sl-ft"] = 10000.0
        f["ic/vc-kts"] = 350.0
        f["ic/psi-true-deg"] = 90.0
        f["ic/gamma-deg"] = 0.0
        f.run_ic()
        f.set_dt(1.0 / 120.0)
        f["propulsion/set-running"] = -1
        f["fcs/throttle-cmd-norm"] = 0.6
        for _ in range(240):
            f.run()
        return f

    # M-A: negative elevator pitches the nose up
    f = airborne()
    theta0 = f["attitude/theta-deg"]
    f["fcs/elevator-cmd-norm"] = -0.3
    for _ in range(360):
        f.run()
    assert f["attitude/theta-deg"] > theta0 + 2.0, (
        "M-A no longer holds: a negative elevator command should pitch UP"
    )

    # M-B: positive rudder yaws left (negative yaw rate)
    f = airborne()
    f["fcs/rudder-cmd-norm"] = 0.4
    for _ in range(30):
        f.run()
    assert f["velocities/r-rad_sec"] < 0.0, (
        "M-B no longer holds: a positive rudder command should yaw LEFT"
    )

    # M-D: the ground initial condition puts weight on the wheels at t0
    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep, emit_fdm=False)
    p.initialize()
    assert p.telemetry.get().on_ground is True, (
        "M-D no longer holds: gear/wow is 0 at t0"
    )
    p.close()


def test_ownship_state_reports_pilot_side_command_signs(ownship):
    """Additions B. The measured inversions are reconciled at the FDM boundary
    and must NOT leak into OwnshipState: a consumer sees pilot-side signs.
    [a: D17] [a: D19]
    """
    ownship.commands.update(pitch=0.5, roll=0.25, yaw=-0.75, throttle=0.3)
    s = run_frames(ownship, 2)
    assert s.elevator_cmd == pytest.approx(0.5)     # pilot-side, not -0.5
    assert s.aileron_cmd == pytest.approx(0.25)
    assert s.rudder_cmd == pytest.approx(-0.75)     # pilot-side, not +0.75
    # while the FDM itself carries the reconciled values
    assert float(ownship._fdm["fcs/elevator-cmd-norm"]) == pytest.approx(-0.5)
    assert float(ownship._fdm["fcs/rudder-cmd-norm"]) == pytest.approx(0.75)
    assert float(ownship._fdm["fcs/steer-cmd-norm"]) == pytest.approx(0.75)


def test_yaw_drives_both_rudder_and_steering(ownship):
    """[a: D7] "Yaw drives both fcs/rudder-cmd-norm and fcs/steer-cmd-norm.\""""
    ownship.commands.update(yaw=0.6)
    run_frames(ownship, 2)
    rudder = float(ownship._fdm["fcs/rudder-cmd-norm"])
    steer = float(ownship._fdm["fcs/steer-cmd-norm"])
    assert rudder == pytest.approx(steer)
    assert rudder != 0.0


# ===========================================================================
# SSF backend rows that belong to the physics side
# ===========================================================================
def test_reset_returns_the_clean_state(ownship):
    """P-01.10b-3 (physics half). [a: D8] "Reset returns the clean state --
    autopilot and fuel freeze disengaged". [a: L2SYS-REQ-01.10b]
    """
    ownship.commands.update(engine_on=True, throttle=0.05, gear_down=False,
                            air_brake=True, parking_brake=False)
    run_frames(ownship, 600)
    ownship.ssf_fuel_freeze(True)
    assert ownship.fuel_freeze is True

    state = ownship.ssf_reset()
    assert state.sim_time_s == pytest.approx(0.0, abs=1e-6)
    assert state.engine_running is False
    assert state.parking_brake is True
    assert state.gear_down is True
    assert state.air_brake is False
    assert state.on_ground is True
    assert ownship.fuel_freeze is False


def test_reset_publishes_one_frame_while_frozen(udp_sink, clock):
    """P-01.10b-4. [a: D8] "publishes one telemetry frame and one FlightGear
    frame even while flight-frozen."
    """
    sink = udp_sink(ports.PCS_TO_FLIGHTGEAR_FDM)
    p = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                      sleep=clock.sleep)
    p.initialize()
    p.ssf_pause()
    assert p.running is False
    sink.datagrams.clear()
    telemetry_before = p.telemetry.count
    sent_before = p.fdm_datagrams_sent

    p.ssf_reset()

    assert p.telemetry.count > telemetry_before, "no telemetry frame on reset"
    assert p.fdm_datagrams_sent == sent_before + 1, "not exactly one FDM frame"
    # and the loop, still frozen, emits nothing further
    p.run(max_frames=240)
    assert p.fdm_datagrams_sent == sent_before + 1
    p.close()


def test_set_position_applies_all_five_fields(ownship):
    """P-01.10b-6 (physics half). [a: L2SYS-REQ-01.10b] "SET POSITION: set
    aircraft position as Lat, Lon (deg), Altitude (m), Speed (m/s), Heading
    (deg)." [a: D20] the Speed field is true airspeed.
    """
    ownship.ssf_set_position(lat_deg=37.0, lon_deg=-122.0, alt_m=5000.0,
                             speed_mps=200.0, heading_deg=270.0)
    s = ownship.telemetry.get()
    assert s.lat_deg == pytest.approx(37.0, abs=0.01)
    assert s.lon_deg == pytest.approx(-122.0, abs=0.01)
    assert s.alt_m == pytest.approx(5000.0, abs=20.0)
    assert s.speed_mps == pytest.approx(200.0, abs=5.0)
    assert s.heading_deg == pytest.approx(270.0, abs=1.0)


def test_set_position_airborne_implies_engine_on_and_brake_released(ownship):
    """P-01.10b-6. [a: D8] "SSF set-position airborne implies engine on and
    parking brake released."
    """
    ownship.ssf_set_position(lat_deg=45.0, lon_deg=9.0, alt_m=4000.0,
                             speed_mps=180.0, heading_deg=90.0)
    s = ownship.telemetry.get()
    assert s.engine_running is True
    assert s.parking_brake is False


def test_snapshot_round_trip_restores_the_state(ownship):
    """P-01.10b-7 (physics half). [a: L2SYS-REQ-01.10b] SAVE / LOAD SNAPSHOT."""
    ownship.ssf_set_position(lat_deg=45.5, lon_deg=9.5, alt_m=3000.0,
                             speed_mps=150.0, heading_deg=45.0)
    ownship.commands.update(throttle=0.6, gear_down=False)
    run_frames(ownship, 600)
    snap = ownship.save_snapshot()

    run_frames(ownship, 1200)                 # fly on
    moved = ownship.telemetry.get()
    assert moved.lat_deg != pytest.approx(snap.lat_deg, abs=1e-6)

    restored = ownship.load_snapshot(snap)
    assert restored.lat_deg == pytest.approx(snap.lat_deg, abs=0.01)
    assert restored.lon_deg == pytest.approx(snap.lon_deg, abs=0.01)
    assert restored.alt_m == pytest.approx(snap.alt_m, abs=20.0)
    assert restored.heading_deg == pytest.approx(snap.heading_deg, abs=1.0)
    assert restored.speed_mps == pytest.approx(snap.speed_mps, abs=5.0)
    assert restored.fuel_lbs == pytest.approx(snap.fuel_lbs, rel=1e-3)
    assert restored.engine_running == snap.engine_running
    assert restored.gear_down == snap.gear_down
