"""PCS UI panels: Sim Control, System Controls, Mission Control,
Navigation/autopilot -- promotes the ad-hoc UI-1 acceptance checks into
the regression suite.
Traces: L2SYS-REQ-01.04b (freeze-gated airport change), 01.06 (System
Controls Panel), 01.06b-e (ENGINE / PARK BRAKE / AIR BRAKE / LDG GEAR
button commands), 01.10 (Sim Control Panel), 01.10b (SSF set: snapshot,
fuel freeze, telemetry capture), 01.11 (Navigation panel: autopilot
targets + AP/AH/AT)."""
import tempfile, time, pytest
from flightsim.common.messages import HotasControls


@pytest.fixture(scope="module")
def pcs(qapp):
    """One MainWindow for the module (binds the real UDP ports)."""
    from flightsim.pcs.main_window import MainWindow
    from conftest import process_events
    win = MainWindow()
    win.show()
    process_events(qapp, 4.0)          # JSBSim init
    yield win
    win.close()


def test_sim_control_panel_and_freeze_gate(qapp, pcs):
    """[01.10] panel exists with the full SSF button set;
    [01.10b/01.04b] freeze gate: RESET/SET POSITION/SAVE/LOAD and the
    airport selector enabled only during flight-freeze."""
    from conftest import process_events
    B = pcs._ssf_buttons
    for key in ("start", "stop", "reset", "otw", "setpos",
                "save", "load", "fuel", "log"):
        assert key in B, f"SSF button {key!r} missing (01.10b)"
    assert pcs._sim_running
    assert not B["reset"].isEnabled() and not B["load"].isEnabled()
    assert B["stop"].isEnabled() and not B["start"].isEnabled()
    assert not pcs.cmb_airport.isEnabled(), "airport gated while running"
    B["stop"].click()
    process_events(qapp, 0.4)
    assert not pcs._sim_running
    assert B["reset"].isEnabled() and B["start"].isEnabled()
    assert pcs.cmb_airport.isEnabled(), "airport free during freeze (01.04b)"
    B["start"].click()
    process_events(qapp, 0.4)
    assert pcs._sim_running


def test_airport_selection_repositions_ownship(qapp, pcs):
    """[01.04] combo selection during freeze repositions the frozen
    aircraft to the chosen airport's runway."""
    from conftest import process_events
    from flightsim.pcs.airports import AIRPORTS
    pcs._ssf_buttons["stop"].click()
    process_events(qapp, 0.3)
    idx = [pcs.cmb_airport.itemData(i)
           for i in range(pcs.cmb_airport.count())].index("KSFO")
    pcs.cmb_airport.setCurrentIndex(idx)
    pcs._ssf_buttons["start"].click()
    process_events(qapp, 2.5)
    tm = pcs.io.last_telemetry
    k = AIRPORTS["KSFO"]
    assert abs(tm.lat_deg - k.lat_deg) < 0.05, tm.lat_deg
    assert abs(tm.lon_deg - k.lon_deg) < 0.05, tm.lon_deg


def test_system_controls_buttons_command_physics(qapp, pcs):
    """[01.06c/d/e] PARK BRAKE / AIR BRAKE / LDG GEAR buttons drive the
    FDM state; button faces resync from actual physics state."""
    from conftest import process_events
    p = pcs.physics
    prk0 = p.parking_brake
    pcs.btn_prk.click(); process_events(qapp, 0.2)
    assert p.parking_brake == (not prk0)
    pcs.btn_prk.click(); process_events(qapp, 0.2)
    assert p.parking_brake == prk0
    g0 = p.gear_down
    pcs.btn_gear.click(); process_events(qapp, 0.2)
    assert p.gear_down == (not g0)
    pcs.btn_gear.click(); process_events(qapp, 0.2)
    assert p.gear_down == g0
    a0 = p.airbrake_out
    pcs.btn_airbrk.click(); process_events(qapp, 0.2)
    assert p.airbrake_out == (not a0)
    pcs.btn_airbrk.click(); process_events(qapp, 0.2)
    assert p.airbrake_out == a0


def test_engine_button_idle_gate_and_start(qapp, pcs):
    """[01.06b] ENGINE toggle-button; [01.07] start refused unless
    throttle at idle, accepted start spools the turbine."""
    from conftest import process_events
    if pcs.physics.engine_on:                 # ensure OFF baseline
        pcs.cb_engine.setChecked(False)
        process_events(qapp, 2.0)
    pcs.io.last_controls = HotasControls(throttle=0.6)
    pcs.cb_engine.setChecked(True)
    process_events(qapp, 0.2)
    assert not pcs.physics.engine_on, "start must be refused at 60% throttle"
    assert not pcs.cb_engine.isChecked(), "button must snap back"
    pcs.io.last_controls = HotasControls(throttle=0.0)
    pcs.cb_engine.setChecked(True)
    process_events(qapp, 3.0)
    assert pcs.physics.engine_on
    assert pcs.physics._fdm["propulsion/engine/n2"] > 40, "must spool to idle"


def test_snapshot_roundtrip(qapp, pcs):
    """[01.10b SAVE/LOAD SNAPSHOT] state captured, mutated, restored --
    and the snapshot must be a copy, not a live reference."""
    snap = pcs._snapshot_state()
    assert "autopilot" in snap and "beacons" in snap and "ownship" in snap
    old = pcs.autopilot.target_alt_m
    pcs.autopilot.target_alt_m = 12345.0
    pcs.apply_snapshot(snap)
    assert pcs.autopilot.target_alt_m == old


def test_telemetry_csv_fields_verbatim(qapp, pcs):
    """[01.10b TELEMETRY CAPTURE] CSV header matches the ssfLogTelemetry
    doc field list exactly (28 fields)."""
    from flightsim.pcs.main_window import TELEMETRY_FIELDS, TelemetryLogger
    tmp = tempfile.mktemp(suffix=".csv")
    lg = TelemetryLogger(); lg.start(tmp)
    lg.log({k: 0 for k in TELEMETRY_FIELDS}); lg.stop()
    hdr = open(tmp).readline().strip().split(",")
    assert hdr == TELEMETRY_FIELDS and len(hdr) == 28


@pytest.mark.slow
def test_autopilot_panel_holds_targets(qapp, pcs):
    """[01.11] AP/AH/AT engaged via the Navigation panel buttons hold
    the set targets on the airborne F16 (tolerances per the tuning
    probe: alt within meters, speed within ~12 m/s, heading converging)."""
    from conftest import process_events
    # airborne start via SSF path
    pcs._ssf_buttons["stop"].click(); process_events(qapp, 0.3)
    pcs.physics.ssf_set_own_position(45.45, 9.28, 3000, 90, 350)
    pcs._ssf_buttons["start"].click(); process_events(qapp, 1.5)
    # sync-on-engage captures current state; set desired targets AFTER
    pcs.cb_ah.setChecked(True)
    pcs.cb_at.setChecked(True)
    pcs.cb_ap.setChecked(True)
    pcs.sp_alt.setValue(3000.0)
    pcs.sp_spd.setValue(180.0)
    pcs.sp_hdg.setValue(120.0)
    tm0 = pcs.io.last_telemetry
    hdg_err0 = abs((120.0 - tm0.heading_deg + 540) % 360 - 180)
    process_events(qapp, 12.0)
    tm = pcs.io.last_telemetry
    hdg_err = abs((120.0 - tm.heading_deg + 540) % 360 - 180)
    assert abs(tm.alt_m - 3000.0) < 30, f"AH: alt err {tm.alt_m-3000:+.0f} m"
    assert abs(tm.groundspeed_ms - 180.0) < 15, \
        f"AT: speed err {tm.groundspeed_ms-180:+.0f} m/s"
    assert hdg_err < hdg_err0, \
        f"AP: heading err must shrink ({hdg_err0:.0f} -> {hdg_err:.0f} deg)"
    # [b: bug fix 2026-07-13, AH damping] cascade law must be settled,
    # not oscillating, by now
    assert abs(tm.vertical_speed_ms) < 3.0, \
        f"AH: must be damped (vs={tm.vertical_speed_ms:+.1f} m/s)"
    for cb in (pcs.cb_ah, pcs.cb_at, pcs.cb_ap):
        cb.setChecked(False)


def test_autopilot_sync_on_engage(qapp, pcs):
    """[bug fix 2026-07-12] engaging a mode captures the CURRENT value as
    target -- no surprise maneuver from stale defaults."""
    from conftest import process_events
    process_events(qapp, 0.5)
    tm = pcs.io.last_telemetry
    pcs.sp_hdg.setValue((tm.heading_deg + 90) % 360)   # stale target
    pcs.cb_ap.setChecked(True)
    assert abs(pcs.sp_hdg.value() - tm.heading_deg) < 2.0, \
        "engage must sync target heading to current"
    pcs.cb_ap.setChecked(False)


def test_reset_gives_clean_starting_condition(qapp, pcs):
    """[bug fix 2026-07-12 / L2SYS-REQ-01.10b RESET] after RESET SIM:
    on runway, engine OFF, PRK BRK ON, AP/AH/AT and fuel freeze
    disengaged, gear down -- no residual state."""
    from conftest import process_events
    from flightsim.pcs.airports import AIRPORTS
    # dirty the state
    pcs.cb_ah.setChecked(True)
    pcs._ssf_buttons["fuel"].setChecked(True)
    pcs.physics.set_parking_brake(False)
    pcs.physics.set_gear(False)
    pcs.physics.set_engine(True)
    process_events(qapp, 0.5)
    # freeze + reset
    pcs._ssf_buttons["stop"].click(); process_events(qapp, 0.3)
    pcs._ssf_buttons["reset"].click(); process_events(qapp, 1.0)
    p = pcs.physics
    apt = AIRPORTS[pcs.selected_icao]
    assert p.engine_on is False, "engine must be OFF after reset"
    assert p.parking_brake is True, "PRK BRK must be ON after reset"
    assert p.gear_down is True
    assert not pcs.autopilot.ah_engaged and not pcs.cb_ah.isChecked()
    assert not pcs._ssf_buttons["fuel"].isChecked()
    # position published even while paused
    tm = pcs.io.last_telemetry
    assert abs(tm.lat_deg - apt.lat_deg) < 0.05, "must sit at the runway"
    assert tm.groundspeed_ms < 1.0
    pcs._ssf_buttons["start"].click(); process_events(qapp, 0.3)