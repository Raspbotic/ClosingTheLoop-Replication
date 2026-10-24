"""Pilot Control Station.

Plan rows: P-01.04-7, P-01.04b-1/-2, P-01.06-1/-2, P-01.06b-1, P-01.06c-1,
P-01.06d-1, P-01.06e-1, P-01.08-1/-2, P-01.08b-1 .. -9, P-01.08c-1 .. -3,
P-01.10-1, P-01.10b-1 .. -11, P-01.11-1/-2/-9, P-02.03-1 .. -4,
P-02.04-1 .. -5.
Additions register group C: test_window_uses_a_dark_stylesheet.

"Where a requirement names a UI element, the test drives the real widget
offscreen, not an internal API." [a: R-8] Every row below clicks the button or
sets the field the requirement names.

Every path the window writes to is inside `tmp_path`. No FlightGear, no
joystick, no external process. [a: D18] [a: Sec.3 criteria 6 and 7]
"""

from __future__ import annotations

import csv
import os

import pytest

from flightsim.common import config, messages, ports
from flightsim.pcs import telemetry as telemetry_mod
from flightsim.pcs import warnings as warn_mod
from flightsim.pcs.airports import SEED_AIRPORTS
from flightsim.pcs.controls import ControlSample
from flightsim.pcs.io_manager import IOManager
from flightsim.pcs.main_window import SSF_COMMANDS, PcsPaths, build_main_window
from flightsim.pcs.physics import PhysicsThread
from flightsim.pcs.virtual_hotas import (
    AFTERBURNER_LABEL,
    THROTTLE_TICKS,
    TITLE_FREE,
    TITLE_LOCKED,
    stick_axes,
)

from test_common_messages import a_state

# [a: D23], transcribed.
D23_DESCENT_MPS = 2.0
D23_TTI_S = 10.0
D23_MAX_AGL_M = 900.0
D23_FLASH_S = 0.6

# [a: D10], transcribed.
D10_WINDOW_S = 1.0
D10_MIRROR_HZ = 10.0


@pytest.fixture
def paths(tmp_path):
    return PcsPaths(
        telemetry_dir=str(tmp_path / "telemetry"),
        snapshot_dir=str(tmp_path / "snapshots"),
        tile_cache_dir=str(tmp_path / "tiles"),
        airports_dir=str(tmp_path),
    )


@pytest.fixture
def window(qt_widgets, paths, clock):
    """A PCS window, offscreen, with every write redirected into tmp_path and
    no FlightGear configured. [a: D15] [a: D18]"""
    launched: list[list[str]] = []
    physics = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                            sleep=clock.sleep, emit_fdm=False)
    io = IOManager(clock=clock.monotonic)
    w = build_main_window(
        paths, physics=physics, io=io, clock=clock.monotonic,
        launcher=launched.append, tile_fetch=lambda url: None,
    )
    w._test_launched = launched
    w._test_clock = clock
    qt_widgets(w)
    yield w


def send(data: bytes, port: int) -> None:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(data, ("127.0.0.1", port))
    finally:
        s.close()


def pump(window, n: int = 5) -> None:
    for _ in range(n):
        window.tick()


# ===========================================================================
# P-01.08 -- the console exists
# ===========================================================================
def test_console_constructs_offscreen(window):
    """P-01.08-1. [a: L2SYS-REQ-01.08] "a virtual cockpit console for the
    pilot, aka PCS or Pilot Control Station." [a: D15] offscreen Qt.
    """
    assert window.windowTitle() == "F-16 PCS (Pilot Control Station)"
    assert window.isEnabled()


def test_console_contains_the_named_panels(window):
    """P-01.08-2. [a: D14] the panels of the reference screenshot."""
    from PyQt5 import QtWidgets

    titles = {b.title() for b in window.findChildren(QtWidgets.QGroupBox)}
    for expected in ("System Controls", "Navigation Controls",
                     "Mission Control", "Sim Control"):
        assert expected in titles, (expected, sorted(titles))


def test_window_uses_a_dark_stylesheet(window):
    """Additions C. [a: D14] "dark QSS"."""
    qss = window.styleSheet()
    assert "background-color: #1e1e1e" in qss
    assert "QGroupBox" in qss


# ===========================================================================
# P-01.06 -- System Controls Panel
# ===========================================================================
def test_system_controls_panel_exists_with_its_four_controls(window):
    """P-01.06-1. [a: L2SYS-REQ-01.06] "The PCS shall provide a System Controls
    Panel." with the four toggles 01.06b-e name.
    """
    from PyQt5 import QtWidgets

    panel = window.findChild(QtWidgets.QGroupBox, "systemControls")
    assert panel is not None
    buttons = panel.findChildren(QtWidgets.QPushButton)
    texts = " ".join(b.text().upper() for b in buttons)
    assert len(buttons) == 4
    for token in ("PRK BRK", "ENGINE", "AIR BRAKE", "GEAR"):
        assert token in texts, (token, texts)


def test_system_control_buttons_are_state_coloured(window):
    """P-01.06-2. [a: D14] "state-coloured PRK BRK / ENGINE / AIR BRAKE /
    GEAR". Each click changes the text between its two stated states and the
    colour with it.
    """
    for button, on, off in (
        (window.btn_park_brake, "PRK BRK: ON", "PRK BRK: OFF"),
        (window.btn_engine, "ENGINE: ON", "ENGINE: OFF"),
        (window.btn_air_brake, "AIR BRAKE: ON", "AIR BRAKE: OFF"),
        (window.btn_gear, "GEAR: DOWN", "GEAR: UP"),
    ):
        before = button.text()
        style_before = button.styleSheet()
        assert before in (on, off), before
        button.click()
        after = button.text()
        assert after != before, button.objectName()
        assert after in (on, off), after
        assert button.styleSheet() != style_before, (
            f"{button.objectName()} did not change colour with its state"
        )
        button.click()                      # back to where it started


# ===========================================================================
# P-01.06b/c/d/e -- the four toggles command the aircraft
# ===========================================================================
def test_engine_button_commands_engine_state(window):
    """P-01.06b-1. [a: L2SYS-REQ-01.06b] the ENGINE toggle-button (ON/OFF)."""
    assert window.physics.commands.get().engine_on is False
    window.btn_engine.click()
    assert window.physics.commands.get().engine_on is True
    assert "ENGINE: ON" in window.btn_engine.text()
    window.btn_engine.click()
    assert window.physics.commands.get().engine_on is False


def test_park_brake_button_commands_park_brake(window):
    """P-01.06c-1. [a: L2SYS-REQ-01.06c] the PARK BRAKE toggle-button."""
    assert window.physics.commands.get().parking_brake is True
    window.btn_park_brake.click()
    assert window.physics.commands.get().parking_brake is False
    assert "PRK BRK: OFF" in window.btn_park_brake.text()


def test_air_brake_button_commands_air_brake(window):
    """P-01.06d-1. [a: L2SYS-REQ-01.06d] the AIR BRAKE toggle-button."""
    assert window.physics.commands.get().air_brake is False
    window.btn_air_brake.click()
    assert window.physics.commands.get().air_brake is True
    pump(window)
    assert window.physics.telemetry.get().air_brake is True


def test_gear_button_commands_gear(window):
    """P-01.06e-1. [a: L2SYS-REQ-01.06e] the LDG GEAR toggle-button
    (UP/DOWN)."""
    assert window.physics.commands.get().gear_down is True
    window.btn_gear.click()
    assert window.physics.commands.get().gear_down is False
    assert "GEAR: UP" in window.btn_gear.text()


# ===========================================================================
# P-01.04-7 / P-01.04b -- Mission Control and the freeze gate
# ===========================================================================
def test_airport_combo_lists_the_configured_airports(window):
    """P-01.04-7. [a: L2SYS-REQ-01.04] "from a configurable airport list"."""
    labels = [window.airport_combo.itemText(i)
              for i in range(window.airport_combo.count())]
    assert labels == [a.label() for a in window.airports]
    assert len(labels) == 4


def test_airport_combo_is_disabled_while_running(window):
    """P-01.04b-1. [a: L2SYS-REQ-01.04b] "The airport selection shall be
    changeable only during flight-freeze."
    """
    assert window.airport_combo.isEnabled(), "frozen at construction"
    window.ssf_buttons["ssf_run"].click()
    assert not window.airport_combo.isEnabled()
    window.ssf_buttons["ssf_pause"].click()
    assert window.airport_combo.isEnabled()


def test_airport_change_while_running_does_not_reposition(window):
    """P-01.04b-2. Forcing the combo's index while running must not move the
    ownship. [a: L2SYS-REQ-01.04b]
    """
    window.ssf_buttons["ssf_run"].click()
    pump(window, 10)
    before = window.last_state
    window.airport_combo.setCurrentIndex(2)          # KSFO, forced
    pump(window, 5)
    after = window.last_state
    assert after.lat_deg == pytest.approx(before.lat_deg, abs=0.05)
    assert after.lon_deg == pytest.approx(before.lon_deg, abs=0.05)


def test_airport_change_while_frozen_repositions(window):
    """P-01.04-6/-7. The other half: while frozen, the selection does take
    effect. [a: L2SYS-REQ-01.04]"""
    window.airport_combo.setCurrentIndex(2)          # KSFO
    ksfo = SEED_AIRPORTS[2]
    assert window.last_state.lat_deg == pytest.approx(ksfo.lat_deg, abs=0.01)
    assert window.last_state.lon_deg == pytest.approx(ksfo.lon_deg, abs=0.01)


# ===========================================================================
# P-01.10 / P-01.10b -- the Sim Control Panel and the nine SSF
# ===========================================================================
def test_sim_control_panel_has_every_ssf_control(window):
    """P-01.10-1. [a: L2SYS-REQ-01.10] "a Sim Control Panel for the selection
    of SSF", with a control for each of the nine SSF of 01.10b.
    """
    from PyQt5 import QtWidgets

    panel = window.findChild(QtWidgets.QGroupBox, "simControl")
    assert panel is not None
    labels = {b.text() for b in panel.findChildren(QtWidgets.QPushButton)}
    for expected in ("RUN SIM", "PAUSE SIM", "RESET SIM", "LAUNCH 3D OTW",
                     "SET POSITION", "Save Snapshot", "Load Snapshot",
                     "FUEL FREEZE", "Telemetry Capture"):
        assert expected in labels, (expected, sorted(labels))
    assert len(SSF_COMMANDS) == 9


def test_run_sim_starts_and_resumes(window):
    """P-01.10b-1. [a: L2SYS-REQ-01.10b] "RUN SIM: start/resume simulation.\""""
    before = window.last_state.sim_time_s
    window.ssf_buttons["ssf_run"].click()
    pump(window, 20)
    assert window.last_state.sim_time_s > before


def test_pause_sim_freezes(window):
    """P-01.10b-2. [a: L2SYS-REQ-01.10b] "PAUSE SIM: pause simulation
    (flight-freeze).\""""
    window.ssf_buttons["ssf_run"].click()
    pump(window, 10)
    window.ssf_buttons["ssf_pause"].click()
    frozen_at = window.last_state.sim_time_s
    pump(window, 30)
    assert window.last_state.sim_time_s == pytest.approx(frozen_at)


def test_reset_sim_returns_to_t0(window):
    """P-01.10b-3. [a: L2SYS-REQ-01.10b] "RESET SIM: reset simulation to
    initial state (t0)." [a: D8] the clean state.
    """
    window.ssf_buttons["ssf_run"].click()
    window.btn_engine.click()
    window.btn_park_brake.click()
    window.btn_gear.click()
    pump(window, 60)
    window.btn_ah.click()                     # engage an autopilot mode
    assert window.autopilot.modes.ah is True

    window.ssf_buttons["ssf_pause"].click()
    window.ssf_buttons["ssf_reset"].click()

    s = window.last_state
    assert s.sim_time_s == pytest.approx(0.0, abs=1e-6)
    assert s.on_ground is True
    assert s.engine_running is False
    assert s.parking_brake is True
    assert s.gear_down is True
    assert window.autopilot.modes.any_engaged is False    # [a: D8]
    assert window.physics.fuel_freeze is False            # [a: D8]
    assert s.lat_deg == pytest.approx(SEED_AIRPORTS[0].lat_deg, abs=0.01)


def test_reset_publishes_one_frame_while_frozen(window, udp_sink):
    """P-01.10b-4. [a: D8] "publishes one telemetry frame and one FlightGear
    frame even while flight-frozen."
    """
    sink = udp_sink(ports.PCS_TO_ORCH)
    window.ssf_buttons["ssf_pause"].click()
    sink.datagrams.clear()
    published_before = window.io.published

    window.ssf_buttons["ssf_reset"].click()

    assert window.io.published == published_before + 1
    assert sink.wait_for(1), "no telemetry frame was published on reset"
    assert isinstance(messages.decode(sink.datagrams[0]),
                      type(window.last_state))


def test_launch_otw_with_no_flightgear_reports_and_continues(window):
    """P-01.10b-5. [a: R-8] "With none installed it reports which variable to
    set and leaves the simulation running; it never raises and never exits."
    [a: Sec.3 criterion 6] [a: D16]
    """
    assert window.flightgear.resolved is False
    window.ssf_buttons["ssf_run"].click()
    pump(window, 5)
    before = window.last_state.sim_time_s

    window.ssf_buttons["ssf_launch_otw"].click()      # raises nothing

    log = window.log_text
    assert config.ENV_EXECUTABLE in log
    assert config.ENV_HOME in log
    assert window._test_launched == [], "a process was started with no FG"

    pump(window, 20)
    assert window.last_state.sim_time_s > before, (
        "the simulation stopped advancing after a blocked LAUNCH 3D OTW"
    )


def test_launch_otw_with_a_flightgear_shaped_tree_builds_the_d6_command(
    qt_widgets, paths, clock, fake_flightgear_tree
):
    """P-01.10b-5. The other half of the deployment row: with a
    FlightGear-shaped installation present, the D6 command is built and handed
    to the launcher. The "installation" is a fake tree inside tmp_path and the
    launcher is a recorder, so no process is started. [a: D18] [a: D6]
    """
    tree = fake_flightgear_tree()
    launched: list[list[str]] = []
    fg = config.resolve_flightgear(
        env={"FSIM_FG_EXECUTABLE": tree["executable"],
             "FSIM_FG_AIRCRAFT_DIR": tree["aircraft_dir"]},
        path_dirs=[],
    )
    assert fg.resolved is True

    physics = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                            sleep=clock.sleep, emit_fdm=False)
    io = IOManager(clock=clock.monotonic)
    w = qt_widgets(build_main_window(paths, physics=physics, io=io,
                                     clock=clock.monotonic,
                                     launcher=launched.append,
                                     tile_fetch=lambda url: None,
                                     flightgear=fg))
    w.ssf_buttons["ssf_launch_otw"].click()

    assert len(launched) == 1
    argv = launched[0]
    assert argv[0] == tree["executable"]
    assert "--fdm=null" in argv
    assert "--native-fdm=socket,in,60,,5550,udp" in argv
    assert "--aircraft=YF-16" in argv
    assert "--airport=LIML" in argv
    assert "--timeofday=noon" in argv
    assert "--httpd=8080" in argv
    assert "LAUNCHING FLIGHTGEAR" in w.log_text


def test_set_position_applies_lat_lon_alt_speed_heading(window):
    """P-01.10b-6. [a: L2SYS-REQ-01.10b] "SET POSITION: set aircraft position
    as Lat, Lon (deg), Altitude (m), Speed (m/s), Heading (deg)."
    [a: D8] airborne implies engine on and parking brake released.
    """
    window.ssf_buttons["ssf_pause"].click()
    window.field_lat.setText("37.0")
    window.field_lon.setText("-122.0")
    window.target_alt.setValue(5000.0)
    window.target_spd.setValue(200.0)
    window.target_hdg.setValue(270.0)

    window.ssf_buttons["ssf_set_position"].click()

    s = window.last_state
    assert s.lat_deg == pytest.approx(37.0, abs=0.01)
    assert s.lon_deg == pytest.approx(-122.0, abs=0.01)
    assert s.alt_m == pytest.approx(5000.0, abs=20.0)
    assert s.speed_mps == pytest.approx(200.0, abs=5.0)
    assert s.heading_deg == pytest.approx(270.0, abs=1.0)
    assert s.engine_running is True          # [a: D8]
    assert s.parking_brake is False          # [a: D8]


def test_save_and_load_snapshot_round_trip(window, tmp_path):
    """P-01.10b-7. [a: L2SYS-REQ-01.10b] "SAVE SNAPSHOT / LOAD SNAPSHOT:
    save/restore full simulation state."
    """
    window.ssf_buttons["ssf_pause"].click()
    window.field_lat.setText("45.6")
    window.field_lon.setText("9.5")
    window.target_alt.setValue(3000.0)
    window.target_spd.setValue(150.0)
    window.target_hdg.setValue(45.0)
    window.ssf_buttons["ssf_set_position"].click()
    window.btn_ah.click()

    path = window.ssf_save_snapshot()
    assert path and os.path.isfile(path)
    assert str(tmp_path) in path, "the snapshot was written outside tmp_path"
    saved = window.last_state

    window.ssf_buttons["ssf_run"].click()
    pump(window, 120)
    window.ssf_buttons["ssf_pause"].click()
    assert window.last_state.sim_time_s > saved.sim_time_s

    assert window.ssf_load_snapshot(path) is True
    restored = window.last_state
    assert restored.lat_deg == pytest.approx(saved.lat_deg, abs=0.01)
    assert restored.lon_deg == pytest.approx(saved.lon_deg, abs=0.01)
    assert restored.alt_m == pytest.approx(saved.alt_m, abs=20.0)
    assert restored.heading_deg == pytest.approx(saved.heading_deg, abs=1.0)
    assert restored.speed_mps == pytest.approx(saved.speed_mps, abs=5.0)
    assert restored.fuel_lbs == pytest.approx(saved.fuel_lbs, rel=1e-3)
    assert restored.gear_down == saved.gear_down
    assert restored.engine_running == saved.engine_running
    assert window.autopilot.modes.ah is True         # the mode came back too


def test_fuel_freeze_holds_tank_contents(window):
    """P-01.10b-8. [a: L2SYS-REQ-01.10b] "FUEL FREEZE: hold tank contents
    constant.\""""
    window.btn_engine.click()
    window.ssf_buttons["ssf_run"].click()
    pump(window, 60)

    window.ssf_buttons["ssf_fuel_freeze"].click()
    assert window.physics.fuel_freeze is True
    frozen_at = window.last_state.fuel_lbs
    pump(window, 200)
    assert window.last_state.fuel_lbs == pytest.approx(frozen_at, abs=0.5)

    window.ssf_buttons["ssf_fuel_freeze"].click()
    assert window.physics.fuel_freeze is False


def test_telemetry_capture_writes_the_csv(window, tmp_path):
    """P-01.10b-9. [a: L2SYS-REQ-01.10b] "TELEMETRY CAPTURE: log telemetry to
    CSV file." [a: D14] the 28-field header, in order.
    """
    window.ssf_buttons["ssf_telemetry_capture"].click()
    assert window.capture.engaged is True
    path = window.capture.path
    window.ssf_buttons["ssf_run"].click()
    pump(window, 30)
    window.ssf_buttons["ssf_telemetry_capture"].click()
    assert window.capture.engaged is False

    assert str(tmp_path) in path, "the CSV was written outside tmp_path"
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    # [a: D14] the header, transcribed here rather than imported
    expected_header = [
        "wall_time_iso", "sim_time_s", "model", "lat_deg", "lon_deg", "alt_m",
        "agl_m", "v_speed_mps", "speed_mps", "heading_deg",
        "target_heading_deg", "pitch_deg", "pitch_rate_dps", "roll_deg",
        "target_alt_m", "target_speed_mps", "aileron_cmd", "elevator_cmd",
        "rudder_cmd", "throttle_cmd", "gear_cmd", "trim_pitch", "trim_roll",
        "trim_yaw", "ap_engaged", "ah_engaged", "at_engaged", "warning_state",
    ]
    assert len(expected_header) == 28
    assert rows[0] == expected_header
    assert len(rows) > 1, "no data rows were written"
    assert all(len(r) == 28 for r in rows[1:])


def test_telemetry_field_list_matches_d14():
    """P-01.10b-9. The module's own list against D14's, independent of a run."""
    assert len(telemetry_mod.TELEMETRY_FIELDS) == 28
    assert telemetry_mod.TELEMETRY_FIELDS[0] == "wall_time_iso"
    assert telemetry_mod.TELEMETRY_FIELDS[-1] == "warning_state"
    assert "v_speed_mps" in telemetry_mod.TELEMETRY_FIELDS      # D14's spelling
    assert "vspeed_mps" not in telemetry_mod.TELEMETRY_FIELDS   # not D19's


def test_ssf_grid_and_menu_are_identical(window):
    """P-01.10b-10. [a: D14] "The SSF grid and the SSF menu command identical
    behaviour."

    Asserted structurally *and* behaviourally: every SSF has both a button and
    a menu action, they are bound to the same callable, and triggering either
    from the same starting state leaves the same state.
    """
    assert set(window.ssf_buttons) == set(window.ssf_actions)
    assert len(window.ssf_buttons) == 9

    for _label, method, _gated in SSF_COMMANDS:
        assert window.ssf_buttons[method] is not None
        assert window.ssf_actions[method] is not None

    def snapshot():
        s = window.last_state
        return (window.physics.running, window.physics.fuel_freeze,
                window.capture.engaged, round(s.sim_time_s, 6),
                s.engine_running, s.gear_down)

    for _label, method, gated in SSF_COMMANDS:
        if method in ("ssf_save_snapshot", "ssf_load_snapshot"):
            continue                       # they touch files; covered above
        window.ssf_pause()
        base = snapshot()
        window.ssf_buttons[method].click()
        via_button = snapshot()

        # restore, then do the same thing through the menu
        window.ssf_pause()
        window.physics.ssf_fuel_freeze(base[1])
        if window.capture.engaged != base[2]:
            window.ssf_telemetry_capture()
        window.ssf_actions[method].trigger()
        via_menu = snapshot()

        assert via_button == via_menu, (
            f"{method}: button gave {via_button}, menu gave {via_menu}"
        )
    window.capture.stop()


def test_freeze_gated_ssf_are_refused_while_running(window):
    """P-01.10b-11. [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml] "can be applied
    only during a flight-freeze condition, i.e. after a ssfStopSimulation has
    been executed".
    """
    window.ssf_buttons["ssf_run"].click()
    pump(window, 10)
    before = window.last_state

    for method in ("ssf_reset", "ssf_set_position", "ssf_save_snapshot",
                   "ssf_load_snapshot"):
        assert not window.ssf_buttons[method].isEnabled(), method
        assert not window.ssf_actions[method].isEnabled(), method
        getattr(window, method)()             # called directly: still refused
        assert "requires a flight freeze" in window.log_text

    after = window.last_state
    assert after.sim_time_s >= before.sim_time_s
    assert after.on_ground == before.on_ground


# ===========================================================================
# P-01.11 -- Navigation Controls Panel
# ===========================================================================
def test_navigation_panel_has_targets_and_three_mode_buttons(window):
    """P-01.11-1. [a: L2SYS-REQ-01.11] "setting the autopilot targets -
    Altitude (m), Speed (m/s), Heading (deg) - and ... the AP ..., AH ... and
    AT ... modes."
    """
    from PyQt5 import QtWidgets

    panel = window.findChild(QtWidgets.QGroupBox, "navigationControls")
    assert panel is not None
    assert panel.findChild(QtWidgets.QDoubleSpinBox, "targetAlt") is not None
    assert panel.findChild(QtWidgets.QDoubleSpinBox, "targetSpd") is not None
    assert panel.findChild(QtWidgets.QDoubleSpinBox, "targetHdg") is not None
    labels = {b.text() for b in panel.findChildren(QtWidgets.QPushButton)}
    assert {"AP", "AH", "AT"} <= labels


def test_engaging_captures_the_current_value_as_target(window):
    """P-01.11-2. [a: D11] "Engaging AP, AH or AT captures the current value as
    target." Driven through the real buttons. [a: R-8]
    """
    window.ssf_buttons["ssf_pause"].click()
    window.field_lat.setText("45.0")
    window.field_lon.setText("9.0")
    window.target_alt.setValue(4000.0)
    window.target_spd.setValue(180.0)
    window.target_hdg.setValue(120.0)
    window.ssf_buttons["ssf_set_position"].click()
    s = window.last_state

    window.target_alt.setValue(0.0)       # scramble the fields first
    window.target_spd.setValue(0.0)
    window.target_hdg.setValue(0.0)

    window.btn_ah.click()
    window.btn_ap.click()
    window.btn_at.click()

    assert window.autopilot.targets.altitude_m == pytest.approx(s.alt_m,
                                                                abs=1.0)
    assert window.autopilot.targets.heading_deg == pytest.approx(
        s.heading_deg, abs=1.0)
    assert window.autopilot.targets.speed_mps == pytest.approx(s.speed_mps,
                                                               abs=1.0)
    # and the panel shows what was captured
    assert window.target_alt.value() == pytest.approx(s.alt_m, abs=1.0)


def test_mode_buttons_toggle_engagement(window):
    """P-01.11-1. "engaging/disengaging". [a: L2SYS-REQ-01.11]"""
    for button, name in ((window.btn_ap, "ap"), (window.btn_ah, "ah"),
                         (window.btn_at, "at")):
        assert getattr(window.autopilot.modes, name) is False
        button.click()
        assert getattr(window.autopilot.modes, name) is True
        assert button.isChecked() is True
        button.click()
        assert getattr(window.autopilot.modes, name) is False


@pytest.mark.slow
def test_engaged_modes_drive_the_aircraft(window):
    """P-01.11-9, **as amended by A-1's sibling note**: the run is in simulated
    time, driven by the injected clock, not wall time. [a: D18]

    Engaging AH with a target above the aircraft reduces the altitude error.
    [a: L2SYS-REQ-01.11] [a: D11]
    """
    window.ssf_buttons["ssf_pause"].click()
    window.field_lat.setText("45.0")
    window.field_lon.setText("9.0")
    window.target_alt.setValue(3000.0)
    window.target_spd.setValue(200.0)
    window.target_hdg.setValue(0.0)
    window.ssf_buttons["ssf_set_position"].click()
    window.ssf_buttons["ssf_run"].click()

    window.autopilot.engage_ah(window.last_state.alt_m + 200.0)
    window.autopilot.engage_at(220.0)
    window._sync_mode_buttons()
    start_error = abs(window.autopilot.targets.altitude_m
                      - window.last_state.alt_m)

    for _ in range(4000):
        window.tick()

    end_error = abs(window.autopilot.targets.altitude_m
                    - window.last_state.alt_m)
    assert end_error < start_error, (start_error, end_error)


# ===========================================================================
# P-01.08b -- virtual controls and physical precedence
# ===========================================================================
def test_virtual_controls_drive_the_aircraft_with_no_physical_input(window):
    """P-01.08b-1. [a: L2SYS-REQ-01.08b] "usable when the physical HOTAS is
    absent"."""
    window.virtual_hotas.stick.roll = 0.4
    window.virtual_hotas.stick.pitch = -0.25
    window.virtual_hotas.throttle.setValue(60)
    window.tick()

    c = window.physics.commands.get()
    assert c.roll == pytest.approx(0.4)
    assert c.pitch == pytest.approx(-0.25)
    assert c.throttle == pytest.approx(0.6)


def test_physical_input_takes_precedence(window):
    """P-01.08b-2. [a: L2SYS-REQ-01.08b] "physical HOTAS input shall take
    precedence when present." [a: D10]
    """
    window.virtual_hotas.stick.roll = 0.9
    window.virtual_hotas.throttle.setValue(10)
    window.tick()
    assert window.physics.commands.get().roll == pytest.approx(0.9)

    controls = messages.HotasControls(seq=1, roll=-0.5, pitch=0.25, yaw=0.1,
                                      throttle=0.8, switches={})
    send(messages.encode(controls), ports.HOTAS_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.io.physical_active:
            break
    assert window.io.physical_active, "the datagram never arrived"

    c = window.physics.commands.get()
    assert c.roll == pytest.approx(-0.5)
    assert c.pitch == pytest.approx(0.25)
    assert c.throttle == pytest.approx(0.8)


def test_precedence_window_is_one_second(window):
    """P-01.08b-3. [a: D10] "Physical-precedence window 1.0 s". Measured
    against the injected clock, not by sleeping. [a: D18]
    """
    from flightsim.pcs import controls as controls_mod

    assert controls_mod.PRECEDENCE_WINDOW_S == D10_WINDOW_S == 1.0

    controls = messages.HotasControls(seq=1, roll=-0.5, pitch=0.0, yaw=0.0,
                                      throttle=0.8, switches={})
    send(messages.encode(controls), ports.HOTAS_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.io.physical_active:
            break
    assert window.io.physical_active

    window._test_clock.advance(0.5)
    assert window.io.physical_active is True, "at +0.5 s, still physical"

    window._test_clock.advance(1.0)          # now +1.5 s
    assert window.io.physical_active is False, "at +1.5 s, back to virtual"


def test_widget_greys_out_and_retitles_while_locked(window):
    """P-01.08b-4. [a: D10] "the widget greys out and its title changes"."""
    assert window.virtual_hotas.stick_group.title() == TITLE_FREE
    assert window.virtual_hotas.stick.isEnabled()

    controls = messages.HotasControls(seq=1, roll=0.1, pitch=0.0, yaw=0.0,
                                      throttle=0.5, switches={})
    send(messages.encode(controls), ports.HOTAS_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.virtual_hotas.locked:
            break

    assert window.virtual_hotas.locked is True
    assert window.virtual_hotas.stick_group.title() == TITLE_LOCKED
    assert not window.virtual_hotas.stick.isEnabled()
    assert not window.virtual_hotas.throttle.isEnabled()

    window._test_clock.advance(1.5)
    window.tick()
    assert window.virtual_hotas.locked is False
    assert window.virtual_hotas.stick_group.title() == TITLE_FREE
    assert window.virtual_hotas.stick.isEnabled()


def test_stick_and_pedals_centre_on_loss_throttle_retained(window):
    """P-01.08b-5. [a: D10] "On loss of the physical device, stick and pedals
    centre and the throttle retains its last position."
    """
    controls = messages.HotasControls(seq=1, roll=0.7, pitch=-0.6, yaw=0.4,
                                      throttle=0.7, switches={})
    send(messages.encode(controls), ports.HOTAS_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.io.physical_active:
            break
    assert window.physics.commands.get().throttle == pytest.approx(0.7)

    window._test_clock.advance(1.5)          # the device is gone
    window.tick()

    c = window.physics.commands.get()
    assert c.roll == pytest.approx(0.0), "roll did not centre on loss"
    assert c.pitch == pytest.approx(0.0), "pitch did not centre on loss"
    assert c.yaw == pytest.approx(0.0), "yaw did not centre on loss"
    assert c.throttle == pytest.approx(0.7), (
        "the throttle did not retain its last position"
    )


def test_stick_returns_to_neutral_on_mouse_release(window):
    """P-01.08b-6. [a: D10] "Stick and pedals return to neutral on any mouse
    release." Driven through the real widget's mouse events. [a: R-8]
    """
    from PyQt5 import QtCore, QtGui

    pad = window.virtual_hotas.stick
    size = pad.size_px
    press = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonPress, QtCore.QPointF(size * 0.9, size * 0.9),
        QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
    pad.mousePressEvent(press)
    assert pad.roll > 0.5 and pad.pitch > 0.5

    release = QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(size * 0.9,
                                                         size * 0.9),
        QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
    pad.mouseReleaseEvent(release)
    assert pad.roll == pytest.approx(0.0)
    assert pad.pitch == pytest.approx(0.0)

    window.virtual_hotas.pedals.setValue(80)
    window.virtual_hotas.centre_pedals()
    assert window.virtual_hotas.pedals.value() == 0


def test_drag_down_is_pitch_plus_one():
    """P-01.08b-7. [a: D10] "Drag-down = stick back = pitch +1."

    Hand computation over a 200 px pad: the centre is 100, the radius 100.
        y = 200 (bottom)  -> pitch = (200 - 100) / 100 = +1.0
        y = 0   (top)     -> pitch = (0 - 100) / 100   = -1.0
        y = 100 (centre)  -> pitch = 0.0
        y = 150           -> pitch = +0.5
    """
    assert stick_axes(100.0, 200.0, 200.0)[1] == pytest.approx(1.0)
    assert stick_axes(100.0, 0.0, 200.0)[1] == pytest.approx(-1.0)
    assert stick_axes(100.0, 100.0, 200.0)[1] == pytest.approx(0.0)
    assert stick_axes(100.0, 150.0, 200.0)[1] == pytest.approx(0.5)
    # and roll: right is positive
    assert stick_axes(200.0, 100.0, 200.0)[0] == pytest.approx(1.0)
    assert stick_axes(0.0, 100.0, 200.0)[0] == pytest.approx(-1.0)


def test_mirroring_is_display_only(window):
    """P-01.08b-8. [a: D10] "The widget mirrors live physical commands at 10 Hz
    while locked; mirroring is display-only."

    The mirrored widget must never become the source of a command.
    """
    from flightsim.pcs import controls as controls_mod

    assert controls_mod.MIRROR_HZ == D10_MIRROR_HZ == 10.0

    window.virtual_hotas.stick.roll = 0.9        # a stale virtual position
    controls = messages.HotasControls(seq=1, roll=-0.3, pitch=0.2, yaw=0.0,
                                      throttle=0.4, switches={})
    send(messages.encode(controls), ports.HOTAS_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.virtual_hotas.locked:
            break

    # the widget now SHOWS the physical position
    assert window.virtual_hotas.stick.roll == pytest.approx(-0.3)
    assert window.virtual_hotas.stick.mirrored is True
    # and the aircraft is flown by the datagram, not by the mirrored widget
    for _ in range(5):
        window.tick()
        assert window.physics.commands.get().roll == pytest.approx(-0.3)


def test_throttle_scale_marks(window):
    """P-01.08b-9. [a: D10] "Throttle scale IDLE 0 / 25 / 50 / 75 / MIL 100
    with an afterburner marker at the top of travel."
    """
    assert [v for v, _ in THROTTLE_TICKS] == [0, 25, 50, 75, 100]
    assert [label for _, label in THROTTLE_TICKS] == ["IDLE", "25", "50", "75",
                                                      "MIL"]
    scale = window.virtual_hotas.throttle_scale.text()
    for label in ("IDLE", "25", "50", "75", "MIL"):
        assert label in scale
    assert window.virtual_hotas.afterburner.text() == AFTERBURNER_LABEL
    assert window.virtual_hotas.throttle.maximum() == 100


# ===========================================================================
# P-01.08c -- gauges
# ===========================================================================
def test_every_named_gauge_exists(window):
    """P-01.08c-1. [a: L2SYS-REQ-01.08c] "minimum: Airspeed, Attitude, Fuel,
    Heading, RPM, HUD view, Attention Getters, and Message Window."
    [a: D14] plus Altitude.
    """
    for name in ("Airspeed", "Attitude", "Fuel", "Heading", "RPM", "Altitude"):
        assert name in window.gauges, name
    assert window.hud is not None                    # HUD view
    assert window.attention_label is not None        # Attention Getters
    assert window.message_log is not None            # Message Window


def test_gauges_track_the_published_state(window):
    """P-01.08c-2. Each gauge shows the corresponding field of the published
    state. [a: L2SYS-REQ-01.08c]
    """
    state = a_state(alt_m=1234.0, speed_mps=210.0, heading_deg=123.0,
                    pitch_deg=5.0, roll_deg=-3.0, fuel_lbs=2500.0,
                    rpm_pct=88.0)
    window._on_state(state)

    assert window.gauges["Altitude"].value == pytest.approx(1234.0)
    assert window.gauges["Airspeed"].value == pytest.approx(210.0)
    assert window.gauges["Heading"].value == pytest.approx(123.0)
    assert window.gauges["Attitude"].value == pytest.approx(5.0)
    assert window.gauges["Fuel"].value == pytest.approx(2500.0)
    assert window.gauges["RPM"].value == pytest.approx(88.0)
    # [a: D20] "The Airspeed gauge may additionally display knots."
    assert window.gauges["Airspeed"].second_value == pytest.approx(
        210.0 * 1.9438444924406046, rel=1e-6)
    assert "HDG" in window.hud.text() and "ALT" in window.hud.text()


def test_message_window_records_messages(window):
    """P-01.08c-3. [a: L2SYS-REQ-01.08c] "Message Window"."""
    window.ssf_buttons["ssf_run"].click()
    assert "RUN SIM" in window.log_text
    window.ssf_buttons["ssf_pause"].click()
    assert "PAUSE SIM" in window.log_text


def test_ownship_state_readouts_are_tabulated_and_right_aligned(window):
    """[a: D14] "tabulated Ownship State readouts (fixed-width,
    right-aligned)". Reported as F-4: the reference screenshot shows no such
    panel; it goes in the empty area of Mission Control.
    """
    from PyQt5 import QtCore

    table = window.state_table
    assert table.rowCount() >= 10
    window._on_state(a_state(alt_m=1234.5))
    row = [i for i in range(table.rowCount())
           if table.item(i, 0).text() == "alt_m"][0]
    cell = table.item(row, 1)
    assert "1234.500" in cell.text()
    assert cell.text().startswith(" "), "not right-aligned by padding"
    assert cell.textAlignment() & QtCore.Qt.AlignRight
    assert "monospace" in cell.font().family().lower()


# ===========================================================================
# P-02.03 -- receiving threat detections
# ===========================================================================
def beacon_bytes(beacon_id="B1", classification="FOE", detected=True,
                 active=True):
    return messages.encode(messages.RadarBeacon(
        beacon_id=beacon_id, classification=classification, lat=45.5, lon=9.3,
        alt=0.0, max_range_m=39000.0, detected=detected,
        slant_range_m=12000.0, active=active))


def test_beacon_datagram_reaches_the_pcs_roster(window):
    """P-02.03-1. [a: L2SYS-REQ-02.03] "The PCS shall receive the threat
    detections incoming from the Synthetic Environment." [a: D4] on 6006.
    """
    send(beacon_bytes("B1", "FOE", detected=True), ports.ORCH_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.io.roster:
            break

    roster = window.io.roster
    assert len(roster) == 1
    assert roster[0].beacon_id == "B1"
    assert roster[0].classification == "FOE"
    assert roster[0].detected is True
    # and it reaches the map
    assert [b.beacon_id for b in window.map.mapGuiWorker.beacons] == ["B1"]


def test_tombstone_removes_the_beacon(window):
    """P-02.03-2. [a: D2] "Deletion is a tombstone (active=False).\""""
    send(beacon_bytes("B1"), ports.ORCH_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.io.roster:
            break
    assert len(window.io.roster) == 1

    send(beacon_bytes("B1", active=False), ports.ORCH_TO_PCS)
    for _ in range(50):
        window.tick()
        if not window.io.roster:
            break
    assert window.io.roster == []
    assert window.map.mapGuiWorker.beacons == []


def test_beacon_stale_beyond_five_seconds_is_discarded(window):
    """P-02.03-3. [a: D2] "The receiver discards beacons stale for more than
    5.0 s." Measured against the injected clock. [a: D18]
    """
    send(beacon_bytes("B1"), ports.ORCH_TO_PCS)
    for _ in range(50):
        window.tick()
        if window.io.roster:
            break
    assert len(window.io.roster) == 1

    window._test_clock.advance(4.9)
    window.tick()
    assert len(window.io.roster) == 1, "discarded too early"

    window._test_clock.advance(0.2)          # now 5.1 s
    window.tick()
    assert window.io.roster == [], "not discarded after 5.0 s"


def test_beacon_refreshed_within_five_seconds_is_kept(window):
    """P-02.03-4. [a: D2] the 2.0 s background refresh keeps a beacon alive."""
    for _ in range(5):
        send(beacon_bytes("B1"), ports.ORCH_TO_PCS)
        for _ in range(20):
            window.tick()
            if window.io.roster:
                break
        window._test_clock.advance(2.0)
        window.tick()
        assert len(window.io.roster) == 1


# ===========================================================================
# P-02.04 -- the ground-proximity attention getter
# ===========================================================================
def test_ground_proximity_condition_truth_table():
    """P-02.04-1. [a: D23] [a: L2SYS-REQ-02.04]

    "asserts when the aircraft is airborne, descending faster than 2 m/s, and
    height above ground divided by rate of descent is under 10 s; never above
    900 m AGL; never on the ground."
    """
    C = warn_mod.ground_proximity_condition
    # airborne, descending 10 m/s, 50 m AGL -> 5 s to impact: ASSERTS
    assert C(agl_m=50.0, vspeed_mps=-10.0, on_ground=False) is True
    # on the ground: never
    assert C(agl_m=0.0, vspeed_mps=-10.0, on_ground=True) is False
    # climbing: never
    assert C(agl_m=50.0, vspeed_mps=+10.0, on_ground=False) is False
    # level: never
    assert C(agl_m=50.0, vspeed_mps=0.0, on_ground=False) is False
    # descending slowly (1 m/s): below the 2 m/s threshold
    assert C(agl_m=5.0, vspeed_mps=-1.0, on_ground=False) is False
    # descending fast but high: 5000 m AGL is above the 900 m ceiling
    assert C(agl_m=5000.0, vspeed_mps=-50.0, on_ground=False) is False
    # descending fast, under the ceiling, but 30 s away: 900 / 30 = 30 s
    assert C(agl_m=900.0, vspeed_mps=-30.0, on_ground=False) is False
    # descending fast, under the ceiling, 9 s away: 270 / 30 = 9 s: ASSERTS
    assert C(agl_m=270.0, vspeed_mps=-30.0, on_ground=False) is True
    # on the ground with everything else asserting: still never
    assert C(agl_m=50.0, vspeed_mps=-10.0, on_ground=True) is False


def test_ground_proximity_boundaries():
    """P-02.04-2. The four boundaries, each hand computed. [a: D23]

      * rate of descent exactly 2.0 m/s -- "faster than 2 m/s" is strict
      * time to impact exactly 10.0 s   -- "under 10 s" is strict
      * AGL exactly 900 m               -- "never above 900 m" allows 900
      * on the ground                   -- never
    """
    C = warn_mod.ground_proximity_condition
    assert warn_mod.DESCENT_THRESHOLD_MPS == D23_DESCENT_MPS == 2.0
    assert warn_mod.TIME_TO_IMPACT_LIMIT_S == D23_TTI_S == 10.0
    assert warn_mod.MAX_AGL_M == D23_MAX_AGL_M == 900.0

    # exactly 2.0 m/s of descent, 5 m AGL -> 2.5 s, but the rate is not
    # "faster than" 2 -> does not assert
    assert C(agl_m=5.0, vspeed_mps=-2.0, on_ground=False) is False
    # 2.01 m/s at the same height -> 2.49 s -> asserts
    assert C(agl_m=5.0, vspeed_mps=-2.01, on_ground=False) is True

    # exactly 10 s: 100 m at 10 m/s -> 10.0 s, not "under" 10 -> no
    assert C(agl_m=100.0, vspeed_mps=-10.0, on_ground=False) is False
    # 99 m at 10 m/s -> 9.9 s -> asserts
    assert C(agl_m=99.0, vspeed_mps=-10.0, on_ground=False) is True

    # exactly 900 m AGL, descending 200 m/s -> 4.5 s; 900 is not "above" 900
    assert C(agl_m=900.0, vspeed_mps=-200.0, on_ground=False) is True
    # 900.1 m is above the ceiling
    assert C(agl_m=900.1, vspeed_mps=-200.0, on_ground=False) is False


def test_attention_getter_flashes_at_about_point_six_seconds(clock):
    """P-02.04-3. [a: L2SYS-REQ-02.04] "Attention Getter shall flash
    intermittently while warning condition persists." [a: D23] "a period of
    about 0.6 s". Measured against the row's own clock. [a: D18]
    """
    getter = warn_mod.AttentionGetter(clock=clock.monotonic)
    assert getter.period_s == D23_FLASH_S == 0.6

    getter.update(agl_m=50.0, vspeed_mps=-10.0, on_ground=False)
    assert getter.asserted is True

    for _ in range(600):                     # 3.0 s in 5 ms ticks
        getter.tick()
        clock.advance(0.005)

    # one period is one on and one off, so 3.0 s at 0.6 s gives 5 cycles and
    # about 10 transitions; the first sample counts as one.
    assert getter.transitions >= 4, getter.transitions
    measured_period = 3.0 / (getter.transitions / 2.0)
    assert measured_period == pytest.approx(0.6, abs=0.12), measured_period


def test_attention_getter_blanks_when_the_condition_clears(clock):
    """P-02.04-4. [a: D23] "blanks otherwise"."""
    getter = warn_mod.AttentionGetter(clock=clock.monotonic)
    getter.update(50.0, -10.0, False)
    assert getter.asserted is True
    assert getter.display_text() == "PULL UP"

    getter.update(5000.0, +5.0, False)
    assert getter.asserted is False
    assert getter.display_text() == ""
    for _ in range(20):
        clock.advance(0.1)
        assert getter.display_text() == "", "it flashed after clearing"


def test_attention_getter_strip_is_empty_in_the_nominal_case(window):
    """P-02.04-5. [a: D14] "The attention-getter strip is empty in the nominal
    case."
    """
    assert window.attention_label.text() == ""
    window.ssf_buttons["ssf_run"].click()
    pump(window, 30)
    assert window.attention_label.text() == "", (
        "the strip showed a warning while sitting on the runway"
    )


def test_attention_getter_reaches_the_window(window):
    """P-02.04-1/-3. The strip in the real window shows the warning when the
    condition holds. [a: L2SYS-REQ-02.04] [a: R-8] real widget, offscreen.
    """
    window._on_state(a_state(agl_m=40.0, vspeed_mps=-12.0, on_ground=False))
    assert window.attention.asserted is True
    assert window.attention_label.text() == "PULL UP"

    window._on_state(a_state(agl_m=4000.0, vspeed_mps=+2.0, on_ground=False))
    assert window.attention.asserted is False
    assert window.attention_label.text() == ""
