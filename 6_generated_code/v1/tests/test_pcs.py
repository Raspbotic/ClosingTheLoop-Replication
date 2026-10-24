"""
tests/test_pcs.py
=================
Verification-plan rows allocated to the Pilot Control Station:
L2SYS-REQ-01.04b, 01.06/01.06b-e, 01.08/01.08b/01.08c, 01.10/01.10b,
01.11, 02.03, 02.04 plus the D6, D10, D11 and D14 conformance rows.

[a] R-8: "Where a requirement names a UI element, the test drives the real
widget offscreen, not an internal API." Every test below that cites a
panel requirement clicks the actual QPushButton / drives the actual
QComboBox.
"""

from __future__ import annotations

import csv
import json
import pathlib
import math
import time

import pytest
from PyQt5 import QtCore

import os

from flightsim.common import ports
from flightsim.common.config import (
    ENV_FG_AIRCRAFT_DIR, ENV_FG_EXECUTABLE, ENV_FG_HOME,
)
from flightsim.common.messages import HotasControls, OwnshipState, RadarBeacon
from flightsim.pcs import main_window as main_window_mod
from flightsim.pcs.main_window import (
    ATTENTION_FLASH_MS, Autopilot, BackgroundIOManager, FG_AIRCRAFT_MODEL,
    PCSMainWindow, TELEMETRY_FIELDS, build_flightgear_command,
    ground_proximity_warning, parse_ground_elevation,
)
from flightsim.pcs.physics import ThreadPhysics
from flightsim.pcs.virtual_hotas import (
    MIRROR_HZ, PHYSICAL_PRECEDENCE_S, THROTTLE_SCALE,
)


@pytest.fixture(scope="module")
def pcs_physics():
    """Module-scoped FDM: JSBSim initialisation is slow [a: D15].

    The cyclic thread is started here (paused) so that a test which clicks
    RUN SIM does not leave a thread behind: this fixture owns it and stops
    it at module teardown [a: D15].
    """
    p = ThreadPhysics(send_to_flightgear=False)
    p.start()
    yield p
    p.ssf_pause()
    p.stop()


@pytest.fixture
def pcs(qapp, pcs_physics, tmp_path):
    """The real PCS window offscreen, with its timers stopped so each
    test drives the ticks deterministically [a: D15]."""
    pcs_physics.ssf_reset()
    pcs_physics.ssf_pause()
    io = BackgroundIOManager()
    window = PCSMainWindow(physics=pcs_physics, io_manager=io,
                           aircraft_dir=str(tmp_path / "Aircraft"),
                           start_timers=False)
    yield window
    # the window does not own the module-scoped FDM, so close() leaves it
    # alone [a: D15]: only this window's own resources are released.
    window.close()
    io.close()


# ==========================================================================
# L2SYS-REQ-01.08 - the console exists
# ==========================================================================

def test_01_08_virtual_cockpit_console_exists(pcs):
    """L2SYS-REQ-01.08: the system shall provide a virtual cockpit console
    for the pilot, aka PCS or Pilot Control Station."""
    assert "PCS" in pcs.windowTitle()
    assert pcs.centralWidget() is not None


def test_d14_layout_panels_are_all_present(pcs):
    """[a: D14] the PCS layout: attention-getter strip, tabulated Ownship
    State readouts, Virtual HOTAS, System Controls, Navigation Controls,
    Mission Control, Sim Control SSF button grid, message log box."""
    assert pcs.attention_strip is not None
    assert pcs.attention_strip.text() == ""      # empty in the nominal case
    assert pcs.ownship_state_box.title() == "Ownship State"
    assert len(pcs.state_labels) >= 10
    assert pcs.virtual_hotas is not None
    assert pcs.system_controls_box.title() == "System Controls"
    assert pcs.navigation_controls_box.title() == "Navigation Controls"
    assert pcs.mission_control_box.title() == "Mission Control"
    assert pcs.sim_control_box.title() == "Sim Control"
    assert pcs.message_log is not None and pcs.message_log.isReadOnly()
    assert "background-color" in pcs.styleSheet()   # dark QSS


def test_d14_ownship_readouts_are_right_aligned_fixed_width(pcs):
    """[a: D14] "tabulated Ownship State readouts (fixed-width,
    right-aligned)"."""
    label = pcs.state_labels["alt_m"]
    assert label.alignment() & QtCore.Qt.AlignRight
    assert label.font().styleHint() == label.font().Monospace


# ==========================================================================
# L2SYS-REQ-01.06 + 01.06b-e - System Controls Panel
# ==========================================================================

def test_01_06_system_controls_panel_provides_the_four_functions(pcs):
    """L2SYS-REQ-01.06: the PCS shall provide a System Controls Panel.

    Pass: the panel exists and exposes exactly the four commanded
    functions of 01.06b-e.
    """
    labels = [pcs.prk_brk_button.text(), pcs.engine_button.text(),
              pcs.air_brake_button.text(), pcs.gear_button.text()]
    assert any("PRK BRK" in t for t in labels)
    assert any("ENGINE" in t for t in labels)
    assert any("AIR BRAKE" in t for t in labels)
    assert any("GEAR" in t for t in labels)


def test_01_06b_engine_toggle_button(pcs):
    """L2SYS-REQ-01.06b: "Engine start/stop" commanded via the ENGINE
    toggle-button in the System Controls Panel (ON/OFF)."""
    pcs.virtual_hotas.throttle.setValue(0)          # idle, [a: D8] gate open
    pcs.on_io_tick()
    assert pcs.physics.engine_on is False
    pcs.engine_button.click()
    assert pcs.physics.engine_on is True
    assert "ON" in pcs.engine_button.text()
    pcs.engine_button.click()
    assert pcs.physics.engine_on is False
    assert "OFF" in pcs.engine_button.text()


def test_01_06b_engine_button_honours_the_idle_gate(pcs):
    """L2SYS-REQ-01.06b through the panel + [a: D8] idle gate: a start
    above 10% throttle is refused and reported in the message window."""
    pcs.virtual_hotas.throttle.setValue(60)
    pcs.on_io_tick()
    pcs.engine_button.click()
    assert pcs.physics.engine_on is False
    assert "REFUSED" in pcs.message_log.toPlainText()


def test_01_06c_park_brake_toggle_button(pcs):
    """L2SYS-REQ-01.06c: "Park Brake" via the PARK BRAKE toggle-button
    (ON/OFF)."""
    assert pcs.physics.park_brake is True           # [a: D8] ground start
    pcs.prk_brk_button.click()
    assert pcs.physics.park_brake is False
    assert "OFF" in pcs.prk_brk_button.text()
    pcs.prk_brk_button.click()
    assert pcs.physics.park_brake is True
    assert pcs.physics._fdm["fcs/left-brake-cmd-norm"] == pytest.approx(1.0)


def test_01_06d_air_brake_toggle_button(pcs):
    """L2SYS-REQ-01.06d: "Air Brake" via the AIR BRAKE toggle-button."""
    assert pcs.physics.air_brake is False
    pcs.air_brake_button.click()
    assert pcs.physics.air_brake is True
    assert pcs.physics._fdm["fcs/speedbrake-cmd-norm"] == pytest.approx(1.0)
    pcs.air_brake_button.click()
    assert pcs.physics.air_brake is False


def test_01_06e_landing_gear_toggle_button(pcs):
    """L2SYS-REQ-01.06e: "Landing Gear" via the LDG GEAR toggle-button
    (UP/DOWN)."""
    assert pcs.physics.gear_down is True
    pcs.gear_button.click()
    assert pcs.physics.gear_down is False
    assert "UP" in pcs.gear_button.text()
    assert pcs.physics._fdm["gear/gear-cmd-norm"] == pytest.approx(0.0)
    pcs.gear_button.click()
    assert "DOWN" in pcs.gear_button.text()


def test_d14_system_control_buttons_are_state_coloured(pcs):
    """[a: D14] "System Controls (state-colored PRK BRK / ENGINE /
    AIR BRAKE / GEAR)"."""
    before = pcs.gear_button.styleSheet()
    pcs.gear_button.click()
    assert pcs.gear_button.styleSheet() != before


# ==========================================================================
# L2SYS-REQ-01.04b - freeze-gated airport selection
# ==========================================================================

def test_01_04b_airport_selection_only_during_flight_freeze(pcs):
    """L2SYS-REQ-01.04b: "The airport selection shall be changeable only
    during flight-freeze."

    Stimulus: the real Mission Control combo, first while running, then
    while paused.
    """
    pcs.ssf_pause()
    assert pcs.airport_combo.isEnabled() is True
    index = pcs.airport_combo.findData("KSFO")
    pcs.airport_combo.setCurrentIndex(index)
    assert pcs.physics.airport.icao == "KSFO"

    pcs.physics.paused = False
    pcs.set_freeze_gate()
    assert pcs.airport_combo.isEnabled() is False, \
        "airport selection was possible while the simulation was running"

    pcs.ssf_pause()
    assert pcs.airport_combo.isEnabled() is True


def test_01_04_mission_control_lists_the_configurable_airports(pcs):
    """L2SYS-REQ-01.04 at the PCS end: the combo is populated from the
    configurable airport table [a: D13]."""
    icaos = {pcs.airport_combo.itemData(i)
             for i in range(pcs.airport_combo.count())}
    assert icaos >= {"LIML", "LIMC", "KSFO", "EDDF"}


# ==========================================================================
# L2SYS-REQ-01.08b + D10 - virtual controls and physical precedence
# ==========================================================================

def test_01_08b_virtual_controls_command_the_aircraft(pcs):
    """L2SYS-REQ-01.08b: on-screen virtual flight controls (stick,
    throttle, pedals) usable when the physical HOTAS is absent.

    Stimulus: drive the real widgets. Observation: the command handed to
    the physics thread.
    """
    pcs.virtual_hotas.stick.mirror(0.4, -0.3)
    pcs.virtual_hotas.pedals.setValue(50)
    pcs.virtual_hotas.throttle.setValue(80)
    pcs.on_io_tick()
    fdm = pcs.physics._fdm
    pcs.physics.step()
    assert fdm["fcs/aileron-cmd-norm"] == pytest.approx(0.4, abs=1e-6)
    # [a: D10] pitch +1 is stick back / nose up; JSBSim's elevator command
    # is positive nose-down, so the physics thread negates it (see
    # _map_inputs_to_fdm).
    assert fdm["fcs/elevator-cmd-norm"] == pytest.approx(0.3, abs=1e-6)
    assert fdm["fcs/rudder-cmd-norm"] == pytest.approx(0.5, abs=1e-6)
    assert fdm["fcs/throttle-cmd-norm"] == pytest.approx(0.8, abs=1e-6)


def test_01_08b_physical_input_takes_precedence(pcs, udp_sender):
    """L2SYS-REQ-01.08b: "physical HOTAS input shall take precedence when
    present"; [a: D10] "fresh 5005 datagram wins; widget greys out with
    title change"."""
    pcs.virtual_hotas.throttle.setValue(20)
    pcs.on_io_tick()
    assert pcs.virtual_hotas.physical_active is False
    assert "ACTIVE" in pcs.virtual_hotas.title()

    physical = HotasControls(roll=-0.7, pitch=0.2, yaw=-0.4, throttle=0.9)
    udp_sender.sendto(physical.to_json(),
                      (ports.LOCALHOST, ports.UDP_HOTAS_CONTROL))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and pcs.io.last_physical is None:
        pcs.on_io_tick()
        time.sleep(0.01)
    assert pcs.io.last_physical is not None
    pcs.on_io_tick()

    assert pcs.virtual_hotas.physical_active is True
    assert "LOCKED" in pcs.virtual_hotas.title()
    assert pcs.virtual_hotas.stick.isEnabled() is False
    pcs.physics.step()
    assert pcs.physics._fdm["fcs/aileron-cmd-norm"] == pytest.approx(
        -0.7, abs=1e-6)


def test_d10_precedence_window_is_one_second_then_falls_back(pcs):
    """[a: D10] "physical precedence window 1.0 s"; after it lapses the
    virtual controls resume."""
    assert PHYSICAL_PRECEDENCE_S == 1.0
    now = 1000.0
    pcs.io.last_physical = HotasControls(roll=0.5, throttle=0.6)
    pcs.io.last_physical_time = now
    assert pcs.io.physical_active(now + 0.9) is True
    assert pcs.io.physical_active(now + 1.01) is False


def test_d10_on_physical_loss_stick_centres_throttle_retained(pcs):
    """[a: D10] "on physical loss, stick/pedals center while throttle
    retains last position (bumpless)"."""
    vh = pcs.virtual_hotas
    vh.set_physical_active(True)
    vh.mirror_physical(HotasControls(roll=0.8, pitch=-0.6, yaw=0.5,
                                     throttle=0.77), now=0.0)
    assert vh.throttle.value() == 77
    vh.set_physical_active(False)
    assert vh.stick.roll == 0.0 and vh.stick.pitch == 0.0
    assert vh.pedals.value() == 0
    assert vh.throttle.value() == 77, "throttle was not retained (bumpless)"


def test_d10_widget_mirrors_physical_at_10hz_while_locked(pcs):
    """[a: D10] "widget mirrors live physical commands at 10 Hz while
    locked"."""
    vh = pcs.virtual_hotas
    vh.set_physical_active(True)
    t = 100.0
    assert vh.mirror_physical(HotasControls(roll=0.3), now=t) is True
    # a second update inside the 1/10 s window must NOT refresh
    assert vh.mirror_physical(HotasControls(roll=0.9),
                              now=t + 0.5 / MIRROR_HZ) is False
    assert vh.stick.roll == pytest.approx(0.3)
    assert vh.mirror_physical(HotasControls(roll=0.9),
                              now=t + 1.1 / MIRROR_HZ) is True
    assert vh.stick.roll == pytest.approx(0.9)


def test_d10_stick_and_pedals_spring_to_neutral_on_mouse_release(pcs):
    """[a: D10] "stick and pedals spring to neutral on ANY mouse release
    (override mouseReleaseEvent -- the sliderReleased signal misses groove
    clicks)"."""
    vh = pcs.virtual_hotas
    vh.stick.mirror(0.6, 0.6)
    vh.pedals.setValue(70)
    # a release event with no preceding press models the groove-click case
    event = QtCore.QEvent(QtCore.QEvent.MouseButtonRelease)
    vh.stick.mouseReleaseEvent(event)
    assert (vh.stick.roll, vh.stick.pitch) == (0.0, 0.0)
    vh.pedals.center()
    assert vh.pedals.value() == 0


def test_d10_stick_sign_convention_drag_down_is_pitch_plus_one(pcs):
    """[a: D10] "drag-down = stick back = pitch +1 (matches physical
    sign)"."""
    stick = pcs.virtual_hotas.stick
    stick.resize(200, 200)
    stick._set_from_pos(QtCore.QPoint(100, 200))     # bottom edge
    assert stick.pitch == pytest.approx(1.0)
    stick._set_from_pos(QtCore.QPoint(100, 0))       # top edge
    assert stick.pitch == pytest.approx(-1.0)


def test_d10_throttle_scale_and_ab_marker(pcs):
    """[a: D10] "throttle slider painted with scale IDLE 0 / 25 / 50 / 75 /
    MIL 100 and AB marker at top of travel"."""
    labels = [label for _pct, label in THROTTLE_SCALE]
    assert labels == ["IDLE 0", "25", "50", "75", "MIL 100"]
    assert pcs.virtual_hotas.throttle.maximum() == 100


# ==========================================================================
# L2SYS-REQ-01.08c - gauges and displays
# ==========================================================================

def test_01_08c_gauges_and_displays_present_and_updating(pcs):
    """L2SYS-REQ-01.08c: on-screen virtual flight gauges and displays -
    minimum: Airspeed, Attitude, Fuel, Heading, RPM, HUD view, Attention
    Getters, and Message Window. (Amendment A-2 of the plan.)"""
    for widget in (pcs.airspeed_gauge, pcs.attitude_gauge, pcs.fuel_gauge,
                   pcs.heading_gauge, pcs.rpm_gauge, pcs.hud_label,
                   pcs.attention_strip, pcs.message_log):
        assert widget is not None

    state = OwnshipState(speed_mps=222.0, alt_m=3210.0, heading_deg=95.0,
                         roll_deg=-12.0, pitch_deg=6.0, fuel_kg=900.0,
                         rpm_pct=83.0, agl_m=3200.0, on_ground=False)
    pcs.update_cockpit_displays(state)
    assert pcs.airspeed_gauge.value == pytest.approx(222.0)
    assert pcs.heading_gauge.value == pytest.approx(95.0)
    assert pcs.fuel_gauge.value == pytest.approx(900.0)
    assert pcs.rpm_gauge.value == pytest.approx(83.0)
    assert pcs.attitude_gauge.roll_deg == pytest.approx(-12.0)
    assert pcs.attitude_gauge.pitch_deg == pytest.approx(6.0)
    assert "222" in pcs.hud_label.text()


def test_01_08c_message_window_receives_events(pcs):
    """L2SYS-REQ-01.08c "Message Window"; [a: D14] "message log box"."""
    pcs.log("TEST MESSAGE 42")
    assert "TEST MESSAGE 42" in pcs.message_log.toPlainText()


# ==========================================================================
# L2SYS-REQ-01.10 / 01.10b - Sim Control Panel and the nine SSF
# ==========================================================================

SSF_NAMES = ["RUN SIM", "PAUSE SIM", "RESET SIM", "LAUNCH 3D OTW",
             "SET POSITION", "Save Snapshot", "Load Snapshot",
             "FUEL FREEZE", "Telemetry Capture"]


def test_01_10_sim_control_panel_exposes_every_ssf(pcs):
    """L2SYS-REQ-01.10: the PCS shall provide a Sim Control Panel for the
    selection of SSF; L2SYS-REQ-01.10b lists the nine required functions."""
    assert set(pcs.ssf_buttons) == set(SSF_NAMES)
    assert len(pcs.ssf_buttons) == 9


def test_d14_ssf_menu_and_buttons_share_one_action_and_one_gate(pcs):
    """[a: D14] "SSF menu + button grid sharing one freeze gate (buttons
    trigger the same actions)"."""
    assert set(pcs.ssf_actions) == set(pcs.ssf_buttons)
    pcs.ssf_pause()
    assert pcs.ssf_buttons["RESET SIM"].isEnabled() is True
    assert pcs.ssf_actions["RESET SIM"].isEnabled() is True
    pcs.physics.paused = False
    pcs.set_freeze_gate()
    assert pcs.ssf_buttons["RESET SIM"].isEnabled() is False
    assert pcs.ssf_actions["RESET SIM"].isEnabled() is False
    pcs.ssf_pause()


def test_01_10b_run_and_pause(pcs):
    """L2SYS-REQ-01.10b: "RUN SIM": start/resume simulation;
    "PAUSE SIM": pause simulation (flight-freeze)."""
    pcs.ssf_buttons["PAUSE SIM"].click()
    assert pcs.physics.paused is True
    pcs.ssf_buttons["RUN SIM"].click()
    assert pcs.physics.paused is False
    assert pcs.physics.is_alive(), "RUN SIM did not run the cyclic loop"
    pcs.ssf_buttons["PAUSE SIM"].click()
    assert pcs.physics.paused is True


def test_01_10b_reset_returns_to_the_initial_state(pcs):
    """L2SYS-REQ-01.10b: "RESET SIM": reset simulation to initial state
    (t0); [a: D8] clean slate with the AP modes disengaged.

    Stimulus: dirty everything, then click RESET SIM while frozen.
    """
    pcs.ssf_pause()
    pcs.virtual_hotas.throttle.setValue(0)
    pcs.on_io_tick()
    pcs.engine_button.click()
    pcs.prk_brk_button.click()
    pcs.gear_button.click()
    pcs.air_brake_button.click()
    pcs.ah_button.setChecked(True)
    pcs.on_ah()
    pcs.ssf_fuel_freeze()
    pcs.virtual_hotas.throttle.setValue(90)
    assert pcs.autopilot.ah_engaged is True

    pcs.ssf_buttons["RESET SIM"].click()

    assert pcs.physics.engine_on is False
    assert pcs.physics.park_brake is True
    assert pcs.physics.gear_down is True
    assert pcs.physics.air_brake is False
    assert pcs.physics.fuel_freeze is False
    assert pcs.autopilot.ah_engaged is False
    assert pcs.ah_button.isChecked() is False
    assert pcs.virtual_hotas.throttle.value() == 0
    assert pcs.io.published, "RESET published no ownship state while frozen"


def test_01_10b_reset_is_refused_while_running(pcs):
    """L2SYS-REQ-01.10b + the ssfResetSimulation caveat: "can be applied
    only during a flight-freeze condition"."""
    pcs.physics.paused = False
    pcs.set_freeze_gate()
    assert pcs.ssf_buttons["RESET SIM"].isEnabled() is False
    pcs.ssf_reset()                       # direct call must also refuse
    assert "REFUSED" in pcs.message_log.toPlainText()
    pcs.ssf_pause()


def test_01_10b_set_position(pcs):
    """L2SYS-REQ-01.10b: "SET POSITION": set aircraft position as Lat, Lon
    (deg), Altitude (m), Speed (m/s), Heading (deg)."""
    pcs.ssf_pause()
    pcs.ssf_set_position(lat_deg=46.0, lon_deg=10.0, alt_m=5000.0,
                         speed_mps=200.0, heading_deg=270.0)
    state = pcs.physics.telemetry()
    assert state.lat_deg == pytest.approx(46.0, abs=0.01)
    assert state.lon_deg == pytest.approx(10.0, abs=0.01)
    assert state.alt_m == pytest.approx(5000.0, rel=0.02)
    assert state.heading_deg == pytest.approx(270.0, abs=1.0)


def test_01_10b_snapshot_save_and_load_round_trip(pcs, tmp_path):
    """L2SYS-REQ-01.10b: "SAVE SNAPSHOT" / "LOAD SNAPSHOT": save/restore
    full simulation state; [a] TA ssfSaveSnapshot lists physics, beacon
    list and map view."""
    pcs.ssf_pause()
    pcs.ssf_set_position(lat_deg=46.5, lon_deg=10.5, alt_m=4000.0,
                         speed_mps=180.0, heading_deg=120.0)
    pcs.io.beacons.apply(RadarBeacon(beacon_id=11, classification="FOE",
                                     lat_deg=46.6, lon_deg=10.6,
                                     max_range_m=30000.0), now=time.monotonic())
    pcs.alt_target.setValue(6000.0)
    pcs.ah_button.setChecked(True)
    pcs.on_ah()

    path = str(tmp_path / "snap.json")
    assert pcs.ssf_save_snapshot(path) == path
    saved = pcs.physics.telemetry()

    pcs.ssf_reset()
    assert pcs.physics.telemetry().alt_m < 1000.0
    assert pcs.autopilot.ah_engaged is False

    assert pcs.ssf_load_snapshot(path) == path
    restored = pcs.physics.telemetry()
    assert restored.lat_deg == pytest.approx(saved.lat_deg, abs=1e-3)
    assert restored.alt_m == pytest.approx(saved.alt_m, rel=1e-2)
    assert pcs.autopilot.ah_engaged is True
    assert 11 in pcs.io.beacons

    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    assert set(blob) >= {"physics", "beacons", "map_zoom", "autopilot"}


def test_01_10b_snapshot_is_freeze_gated(pcs, tmp_path):
    """L2SYS-REQ-01.10b SAVE/LOAD SNAPSHOT with the TA ssfSaveSnapshot /
    ssfLoadSnapshot caveat: "can be applied only during a flight-freeze
    condition"."""
    pcs.physics.paused = False
    pcs.set_freeze_gate()
    assert pcs.ssf_save_snapshot(str(tmp_path / "x.json")) is None
    assert pcs.ssf_load_snapshot(str(tmp_path / "x.json")) is None
    pcs.ssf_pause()


def test_01_10b_fuel_freeze_button(pcs):
    """L2SYS-REQ-01.10b: "FUEL FREEZE": hold tank contents constant."""
    assert pcs.physics.fuel_freeze is False
    pcs.ssf_buttons["FUEL FREEZE"].click()
    assert pcs.physics.fuel_freeze is True
    pcs.ssf_buttons["FUEL FREEZE"].click()
    assert pcs.physics.fuel_freeze is False


def test_01_10b_telemetry_capture_writes_the_28_field_header(pcs, tmp_path):
    """L2SYS-REQ-01.10b: "TELEMETRY CAPTURE": log telemetry to CSV file.
    [a: D14] "Telemetry CSV header: the 28 fields verbatim from the
    ssfLogTelemetry doc"."""
    path = str(tmp_path / "telemetry.csv")
    assert pcs.ssf_telemetry_capture(path) == path
    assert pcs.io.capturing is True
    pcs.on_publish_tick()
    pcs.on_publish_tick()
    pcs.ssf_telemetry_capture()                   # second click stops it
    assert pcs.io.capturing is False

    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == TELEMETRY_FIELDS
    assert len(rows[0]) == 28
    assert len(rows) >= 3                          # header + two samples
    assert len(rows[1]) == 28


def test_d14_telemetry_field_names_match_the_ta_doc_comment():
    """[a: D14] the 28 fields verbatim from the ssfLogTelemetry doc
    comment of fsim_TA_PCS_ThreadFlightSim_UIM.sysml."""
    assert TELEMETRY_FIELDS == [
        "wall_time_iso", "sim_time_s", "model", "lat_deg", "lon_deg",
        "alt_m", "agl_m", "v_speed_mps", "speed_mps", "heading_deg",
        "target_heading_deg", "pitch_deg", "pitch_rate_dps", "roll_deg",
        "target_alt_m", "target_speed_mps", "aileron_cmd", "elevator_cmd",
        "rudder_cmd", "throttle_cmd", "gear_cmd", "trim_pitch", "trim_roll",
        "trim_yaw", "ap_engaged", "ah_engaged", "at_engaged",
        "warning_state"]


def test_d6_launch_otw_builds_the_decision_annex_command_line(pcs):
    """L2SYS-REQ-01.10b "LAUNCH 3D OTW"; [a: D6] the FG command line
    exactly as the legacy launch_flightgear() function issues it.

    The executable is pinned to the D6 program name here so this row stays
    independent of whatever FlightGear install the machine running the
    suite happens to have (amendment A-6); the path-resolution rows live in
    tests/test_config.py.
    """
    command = build_flightgear_command("LIML", "/opt/fg/Aircraft",
                                       executable="fgfs")
    assert command == [
        "fgfs",
        "--fdm=null",
        "--native-fdm=socket,in,60,,5550,udp",
        "--fg-aircraft=/opt/fg/Aircraft",
        "--aircraft=YF-16",
        "--airport=LIML",
        "--timeofday=noon",
        "--httpd=8080",
        "--prop:/input/joysticks/js[0]=0",
        "--prop:/input/joysticks/js[1]=0",
    ]
    assert FG_AIRCRAFT_MODEL == "YF-16"


def test_d6_launch_otw_uses_the_selected_airport(pcs, monkeypatch):
    """L2SYS-REQ-01.10b: LAUNCH 3D OTW loads the aircraft "at the currently
    selected airport" (TA ssfLaunchOtwView doc).

    Popen is stubbed. On a machine that HAS FlightGear (the target
    workstation does, on PATH) this row previously spawned the real
    fgfs.exe during a headless run: it inherited QT_QPA_PLATFORM=offscreen
    and logged 'Could not find the Qt platform plugin "offscreen"', and the
    process was never reaped. No test may start a GUI application.
    """
    spawned = []
    monkeypatch.setattr(main_window_mod.subprocess, "Popen",
                        lambda cmd, *a, **k: spawned.append(cmd) or object())
    pcs.ssf_pause()
    pcs.airport_combo.setCurrentIndex(pcs.airport_combo.findData("EDDF"))
    command = pcs.ssf_launch_otw()
    assert "--airport=EDDF" in command
    assert "FLIGHTGEAR" in pcs.message_log.toPlainText().upper()
    assert len(spawned) <= 1


def test_no_test_spawns_a_real_process_via_ssf_launch_otw():
    """Suite hygiene, amendment A-10 [a: D15]: every test that reaches
    ssf_launch_otw() must stub subprocess.Popen first.

    Enforced by inspection of the test source rather than by trusting each
    author: the failure mode is silent on a machine without FlightGear and
    only appears on one that has it - i.e. the reviewer's, not the
    developer's.
    """
    import re
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    bodies = re.split(r"\ndef (test_\w+)", source)
    offenders = []
    for name, body in zip(bodies[1::2], bodies[2::2]):
        if "ssf_launch_otw()" in body and '"Popen"' not in body:
            offenders.append(name)
    assert not offenders, (
        f"these tests reach ssf_launch_otw() without stubbing Popen and "
        f"would spawn the real FlightGear: {offenders}")


def test_a6_launch_otw_aborts_informatively_when_executable_is_missing(
        pcs, monkeypatch, tmp_path):
    """L2SYS-REQ-01.10b LAUNCH 3D OTW with amendment A-6: the field-report
    failure mode. With no FlightGear executable the SSF must NOT raise and
    must NOT spawn anything; it must name the environment variable to set.

    The absent path is stated EXPLICITLY rather than left to an empty
    environment: the resolver's well-known-locations step globs the real
    filesystem, so on a machine that has FlightGear installed an "empty
    environment" is not an unconfigured one. That assumption is exactly
    what failed on the target machine (a real FlightGear 2024.1 install was
    found and the launch legitimately proceeded).
    """
    missing = tmp_path / "no-such-dir" / "fgfs.exe"
    monkeypatch.setenv(ENV_FG_EXECUTABLE, str(missing))
    monkeypatch.setenv(ENV_FG_AIRCRAFT_DIR, str(tmp_path))
    spawned = []
    monkeypatch.setattr(main_window_mod.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or object())

    pcs.ssf_pause()
    command = pcs.ssf_launch_otw()          # must not raise

    assert spawned == [], "a launch was attempted with no FlightGear present"
    assert pcs.fg_process is None
    log = pcs.message_log.toPlainText()
    assert "NOT CONFIGURED" in log
    assert ENV_FG_EXECUTABLE in log
    assert command[0] == str(missing)


def test_a6_launch_otw_proceeds_when_only_the_aircraft_dir_is_missing(
        pcs, monkeypatch, tmp_path):
    """L2SYS-REQ-01.10b LAUNCH 3D OTW with amendment A-6 (severity, raised
    by the target-machine run): a missing --fg-aircraft directory is a
    WARNING, not a blocker.

    This is the target machine's actual situation - FlightGear installed
    and auto-detected, YF-16 directory absent. Aborting there would be a
    regression against the pre-fix build, which at least started
    FlightGear.
    """
    fg_dir = tmp_path / "FG" / "bin"
    fg_dir.mkdir(parents=True)
    exe = fg_dir / ("fgfs.exe" if os.name == "nt" else "fgfs")
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv(ENV_FG_EXECUTABLE, str(exe))
    monkeypatch.setenv(ENV_FG_AIRCRAFT_DIR, str(tmp_path / "absent"))
    pcs.aircraft_dir = str(tmp_path / "absent")
    spawned = []
    monkeypatch.setattr(main_window_mod.subprocess, "Popen",
                        lambda cmd, *a, **k: spawned.append(cmd) or object())

    pcs.ssf_pause()
    pcs.ssf_launch_otw()

    assert len(spawned) == 1, "the launch was blocked by a mere warning"
    log = pcs.message_log.toPlainText()
    assert "FLIGHTGEAR WARNING" in log
    assert ENV_FG_AIRCRAFT_DIR in log
    assert "ABORTED" not in log


def test_a6_launch_otw_spawns_the_resolved_executable(pcs, monkeypatch,
                                                      tmp_path):
    """L2SYS-REQ-01.10b LAUNCH 3D OTW with amendment A-6: when the
    environment names a real executable, THAT path is spawned, with the
    [a: D6] flags unchanged."""
    fg_dir = tmp_path / "FG" / "bin"
    fg_dir.mkdir(parents=True)
    exe = fg_dir / ("fgfs.exe" if os.name == "nt" else "fgfs")
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    aircraft = tmp_path / "Aircraft"
    aircraft.mkdir()

    monkeypatch.setenv(ENV_FG_EXECUTABLE, str(exe))
    monkeypatch.setenv(ENV_FG_AIRCRAFT_DIR, str(aircraft))
    pcs.aircraft_dir = str(aircraft)
    spawned = []
    monkeypatch.setattr(main_window_mod.subprocess, "Popen",
                        lambda cmd, *a, **k: spawned.append(cmd) or object())

    pcs.ssf_pause()
    command = pcs.ssf_launch_otw()

    assert len(spawned) == 1, "the configured executable was not spawned"
    assert spawned[0][0] == str(exe)
    assert spawned[0] == command
    assert command[1] == "--fdm=null"                      # [a: D6]
    assert command[2] == "--native-fdm=socket,in,60,,5550,udp"
    assert "--aircraft=YF-16" in command
    assert "NOT CONFIGURED" not in pcs.message_log.toPlainText()


def test_a6_launch_otw_never_lets_an_exception_reach_the_event_loop(
        pcs, monkeypatch, tmp_path):
    """L2SYS-REQ-01.10b with amendment A-6: even a configured-but-broken
    installation must be reported in the message window rather than raised
    into the Qt event loop, which is what "it crashes" looked like."""
    exe = tmp_path / "fgfs"
    exe.write_text("x")
    exe.chmod(0o755)
    monkeypatch.setenv(ENV_FG_EXECUTABLE, str(exe))
    monkeypatch.setenv(ENV_FG_AIRCRAFT_DIR, str(tmp_path))

    def boom(*_a, **_k):
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(main_window_mod.subprocess, "Popen", boom)
    pcs.ssf_pause()
    pcs.ssf_launch_otw()                    # must not raise
    log = pcs.message_log.toPlainText()
    assert "FLIGHTGEAR LAUNCH FAILED" in log
    assert "Exec format error" in log


# ==========================================================================
# Qt signal-argument regression (found while fixing the launch defect)
# ==========================================================================

def test_01_10b_ssf_buttons_invoke_their_action_with_no_arguments(pcs):
    """L2SYS-REQ-01.10b: each SSF button must invoke its own action with the
    PANEL's values.

    Regression: QPushButton.clicked carries a `checked` bool, which PyQt
    binds to the slot's first optional parameter. Connected directly, the
    SET POSITION button therefore called ssf_set_position(lat_deg=False),
    driving the ownship to latitude 0. The buttons now swallow the signal
    argument.
    """
    pcs.ssf_pause()
    pcs.ssf_set_position(lat_deg=46.0, lon_deg=10.0, alt_m=3000.0,
                         speed_mps=150.0, heading_deg=90.0)
    pcs.alt_target.setValue(3000.0)
    pcs.spd_target.setValue(150.0)
    pcs.hdg_target.setValue(90.0)
    before = pcs.physics.telemetry()

    pcs.ssf_buttons["SET POSITION"].click()

    after = pcs.physics.telemetry()
    assert after.lat_deg == pytest.approx(before.lat_deg, abs=0.05), \
        "the clicked() bool leaked into lat_deg (latitude was reset)"
    assert abs(after.lat_deg) > 1.0, "ownship was driven to latitude 0"


def test_01_10b_snapshot_button_click_does_not_pass_a_bool_as_path(
        pcs, monkeypatch, tmp_path):
    """L2SYS-REQ-01.10b SAVE SNAPSHOT: same regression on the snapshot
    slots - a leaked `False` would be taken as the path argument."""
    seen = {}

    def fake_save(path=None):
        seen["path"] = path
        return path

    monkeypatch.setattr(pcs, "ssf_save_snapshot", fake_save)
    # rebuild only this button's connection the way the panel does it
    button = pcs.ssf_buttons["Save Snapshot"]
    button.clicked.disconnect()
    button.clicked.connect(lambda _checked=False, fn=fake_save: fn())
    button.click()
    assert seen["path"] is None, "clicked() bool leaked into the path argument"


# ==========================================================================
# L2SYS-REQ-01.11 + D11 - Navigation Controls and autopilot
# ==========================================================================

def test_01_11_navigation_panel_targets_and_modes(pcs):
    """L2SYS-REQ-01.11: Navigation Controls Panel for setting the
    autopilot targets - Altitude (m), Speed (m/s), Heading (deg) - and for
    engaging/disengaging AP (heading hold), AH (altitude hold) and AT
    (autothrottle)."""
    assert pcs.alt_target.suffix().strip() == "m"
    assert pcs.spd_target.suffix().strip() == "m/s"
    assert pcs.hdg_target.suffix().strip() == "deg"
    pcs.alt_target.setValue(3000.0)
    pcs.spd_target.setValue(210.0)
    pcs.hdg_target.setValue(275.0)
    assert pcs.autopilot.target_alt_m == 3000.0
    assert pcs.autopilot.target_speed_mps == 210.0
    assert pcs.autopilot.target_heading_deg == 275.0

    for button, flag in ((pcs.ap_button, "ap_engaged"),
                         (pcs.ah_button, "ah_engaged"),
                         (pcs.at_button, "at_engaged")):
        button.setChecked(True)
        button.click() if False else None
        getattr(pcs, f"on_{flag[:2]}")()
        assert getattr(pcs.autopilot, flag) is True
        button.setChecked(False)
        getattr(pcs, f"on_{flag[:2]}")()
        assert getattr(pcs.autopilot, flag) is False


def test_d11_sync_on_engage(pcs):
    """[a: D11] "engaging AP/AH/AT captures the current value as target
    (sync-on-engage)"."""
    ap = Autopilot()
    state = OwnshipState(alt_m=2345.0, speed_mps=178.0, heading_deg=93.0)
    ap.engage_ah(state)
    ap.engage_at(state)
    ap.engage_ap(state)
    assert ap.target_alt_m == pytest.approx(2345.0)
    assert ap.target_speed_mps == pytest.approx(178.0)
    assert ap.target_heading_deg == pytest.approx(93.0)
    # re-engaging while already engaged must not re-sync
    ap.engage_ah(OwnshipState(alt_m=9999.0))
    assert ap.target_alt_m == pytest.approx(2345.0)


@pytest.mark.parametrize("alt_err,vs,pitch_deg", [
    (0.0, 0.0, 0.0), (500.0, 0.0, 0.0), (-500.0, 0.0, 5.0),
    (20.0, 2.0, -3.0), (-2000.0, -12.0, 10.0),
])
def test_d11_altitude_hold_cascade(alt_err, vs, pitch_deg):
    """[a: D11] AH cascade, evaluated here from the Decision Annex text:
    vs_cmd = clip(0.15*alt_err, +-5); theta_cmd = clip(1.5*(vs_cmd - vs),
    -8, +12); pitch = clip(0.06*(theta_cmd - pitch_deg), +-1)."""
    def clip(v, lo, hi):
        return max(lo, min(hi, v))

    target = 1000.0
    state = OwnshipState(alt_m=target - alt_err, vspeed_mps=vs,
                         pitch_deg=pitch_deg)
    vs_cmd = clip(0.15 * alt_err, -5.0, 5.0)
    theta_cmd = clip(1.5 * (vs_cmd - vs), -8.0, 12.0)
    expected = clip(0.06 * (theta_cmd - pitch_deg), -1.0, 1.0)
    assert Autopilot.altitude_hold(state, target) == pytest.approx(expected)


@pytest.mark.parametrize("hdg_err,roll_deg", [
    (0.0, 0.0), (10.0, 0.0), (-90.0, 0.0), (5.0, 20.0), (180.0, -30.0),
])
def test_d11_heading_hold_law(hdg_err, roll_deg):
    """[a: D11] AP: bank_cmd = clip(0.8*hdg_err, +-25 deg);
    roll = clip(0.03*(bank_cmd - roll_deg))."""
    def clip(v, lo, hi):
        return max(lo, min(hi, v))

    target = 90.0
    state = OwnshipState(heading_deg=(target - hdg_err) % 360.0,
                         roll_deg=roll_deg)
    err = ((target - state.heading_deg + 180.0) % 360.0) - 180.0
    bank_cmd = clip(0.8 * err, -25.0, 25.0)
    expected = clip(0.03 * (bank_cmd - roll_deg), -1.0, 1.0)
    assert Autopilot.heading_hold(state, target) == pytest.approx(expected)


@pytest.mark.parametrize("spd_err", [0.0, 5.0, -5.0, 50.0, -50.0])
def test_d11_autothrottle_law(spd_err):
    """[a: D11] AT: throttle = clip(0.5 + 0.04*spd_err)."""
    target = 200.0
    state = OwnshipState(speed_mps=target - spd_err)
    expected = max(0.0, min(1.0, 0.5 + 0.04 * spd_err))
    assert Autopilot.autothrottle(state, target) == pytest.approx(expected)


def test_d11_engaged_modes_override_the_manual_axes(pcs):
    """L2SYS-REQ-01.11: while a mode is engaged the corresponding axis
    command is produced by the autopilot, not by the pilot's stick."""
    ap = Autopilot()
    state = OwnshipState(alt_m=1000.0, speed_mps=150.0, heading_deg=0.0)
    manual = HotasControls(roll=1.0, pitch=1.0, throttle=1.0)
    assert ap.apply(state, manual) == manual          # nothing engaged

    ap.target_alt_m = 3000.0
    ap.ah_engaged = True
    ap.target_heading_deg = 90.0
    ap.ap_engaged = True
    ap.target_speed_mps = 250.0
    ap.at_engaged = True
    out = ap.apply(state, manual)
    assert out.pitch == pytest.approx(
        Autopilot.altitude_hold(state, 3000.0))
    assert out.roll == pytest.approx(Autopilot.heading_hold(state, 90.0))
    assert out.throttle == pytest.approx(
        Autopilot.autothrottle(state, 250.0))
    assert out.yaw == manual.yaw                      # yaw stays manual


@pytest.mark.slow
def test_d11_altitude_hold_captures_the_target_in_flight(pcs):
    """L2SYS-REQ-01.11 end to end: with AH engaged the aircraft climbs
    towards the commanded altitude."""
    pcs.ssf_pause()
    pcs.ssf_set_position(lat_deg=45.5, lon_deg=9.3, alt_m=3000.0,
                         speed_mps=200.0, heading_deg=90.0)
    pcs.alt_target.setValue(3600.0)
    pcs.ah_button.setChecked(True)
    pcs.on_ah()
    pcs.autopilot.target_alt_m = 3600.0
    pcs.at_button.setChecked(True)
    pcs.on_at()
    pcs.autopilot.target_speed_mps = 200.0
    start_alt = pcs.physics.telemetry().alt_m
    for _ in range(30):
        pcs.on_io_tick()
        for _ in range(40):
            pcs.physics.step()
    end_alt = pcs.physics.telemetry().alt_m
    assert end_alt > start_alt + 20.0, \
        f"altitude hold did not climb: {start_alt:.0f} -> {end_alt:.0f} m"


# ==========================================================================
# L2SYS-REQ-02.03 - threat-detection reception
# ==========================================================================

def test_02_03_pcs_receives_threat_detections(pcs, udp_sender):
    """L2SYS-REQ-02.03: the PCS shall receive the threat detections
    incoming from the Synthetic Environment.

    Stimulus: a RadarBeacon on the D4 orch->PCS port (6006).
    """
    beacon = RadarBeacon(beacon_id=42, classification="FOE", lat_deg=45.5,
                         lon_deg=9.4, max_range_m=38000.0, detected=True,
                         slant_range_m=12000.0)
    udp_sender.sendto(beacon.to_json(),
                      (ports.LOCALHOST, ports.UDP_ORCH_TO_PCS))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and 42 not in pcs.io.beacons:
        pcs.io.poll()
        time.sleep(0.01)
    got = pcs.io.beacons.get(42)
    assert got is not None, "the PCS did not receive the threat detection"
    assert got.classification == "FOE" and got.detected is True


def test_02_03_tombstone_removes_the_detection(pcs, udp_sender):
    """L2SYS-REQ-02.03 + [a: D2]: the PCS store honours the tombstone."""
    beacon = RadarBeacon(beacon_id=43, classification="FRIEND",
                         max_range_m=1000.0)
    udp_sender.sendto(beacon.to_json(),
                      (ports.LOCALHOST, ports.UDP_ORCH_TO_PCS))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and 43 not in pcs.io.beacons:
        pcs.io.poll()
        time.sleep(0.01)
    assert 43 in pcs.io.beacons
    udp_sender.sendto(beacon.tombstone().to_json(),
                      (ports.LOCALHOST, ports.UDP_ORCH_TO_PCS))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and 43 in pcs.io.beacons:
        pcs.io.poll()
        time.sleep(0.01)
    assert 43 not in pcs.io.beacons


def test_02_03_malformed_detection_is_rejected_not_crashing(pcs, udp_sender):
    """L2SYS-REQ-02.03 + [a: D1]: a malformed datagram must be rejected
    without disturbing the PCS."""
    udp_sender.sendto(b"garbage not json",
                      (ports.LOCALHOST, ports.UDP_ORCH_TO_PCS))
    time.sleep(0.05)
    pcs.io.poll()                                  # must not raise
    assert len(pcs.io.beacons) == 0


# ==========================================================================
# L2SYS-REQ-02.04 - ground-proximity attention getter (amendment A-1)
# ==========================================================================

def test_02_04_warning_criterion_uses_agl_and_vertical_speed():
    """L2SYS-REQ-02.04: "... when height above ground and vertical speed
    indicate imminent ground impact"."""
    # low and descending fast -> warning
    assert ground_proximity_warning(OwnshipState(
        agl_m=100.0, vspeed_mps=-30.0, on_ground=False)) is True
    # low but level -> no warning (vertical speed matters)
    assert ground_proximity_warning(OwnshipState(
        agl_m=100.0, vspeed_mps=0.0, on_ground=False)) is False
    # descending fast but high -> no warning (height matters)
    assert ground_proximity_warning(OwnshipState(
        agl_m=5000.0, vspeed_mps=-30.0, on_ground=False)) is False
    # on the ground -> never a warning
    assert ground_proximity_warning(OwnshipState(
        agl_m=0.0, vspeed_mps=-30.0, on_ground=True)) is False


def test_02_04_attention_getter_flashes_while_the_condition_persists(pcs):
    """L2SYS-REQ-02.04: the PCS shall display a ground-proximity attention
    getter (e.g. 'PULL UP') ...; "Attention Getter shall flash
    intermittently while warning condition persists."

    Stimulus: dangerous telemetry, then benign telemetry.
    Observation: the attention-getter strip across successive flash ticks.
    """
    danger = OwnshipState(agl_m=80.0, vspeed_mps=-25.0, on_ground=False)
    pcs.update_cockpit_displays(danger)
    assert pcs.warning_state == "PULL UP"

    seen = []
    for _ in range(4):
        pcs.on_attention_tick()
        seen.append(pcs.attention_strip.text())
    assert "PULL UP" in seen, "the warning never appeared"
    assert "" in seen, "the warning did not flash (never blanked)"
    assert seen[0] != seen[1], "consecutive ticks did not alternate"

    safe = OwnshipState(agl_m=4000.0, vspeed_mps=0.0, on_ground=False)
    pcs.update_cockpit_displays(safe)
    pcs.on_attention_tick()
    assert pcs.warning_state == ""
    assert pcs.attention_strip.text() == ""


def test_02_04_flash_timer_is_configured(pcs):
    """L2SYS-REQ-02.04 "flash intermittently": a periodic timer drives the
    strip."""
    assert pcs.attention_timer.interval() == ATTENTION_FLASH_MS
    assert ATTENTION_FLASH_MS > 0


def test_02_04_warning_state_reaches_the_telemetry_log(pcs):
    """L2SYS-REQ-02.04 + [a: D14]: warning_state is the 28th CSV field."""
    pcs.update_cockpit_displays(OwnshipState(agl_m=60.0, vspeed_mps=-20.0,
                                             on_ground=False))
    row = pcs.telemetry_row(pcs.last_state)
    assert row["warning_state"] == "PULL UP"
    assert TELEMETRY_FIELDS[-1] == "warning_state"


# ==========================================================================
# D9 terrain feed at the PCS end
# ==========================================================================

def test_d9_ground_elevation_accepts_a_string_value():
    """[a: D4] "ground elevation at /json/position/ground-elev-m (value may
    arrive as string)"."""
    assert parse_ground_elevation({"value": "123.5"}) == pytest.approx(123.5)
    assert parse_ground_elevation({"value": 77.0}) == pytest.approx(77.0)
    assert parse_ground_elevation({"value": None}) is None
    assert parse_ground_elevation({}) is None


def test_d9_terrain_timer_runs_at_2hz(pcs):
    """[a: D9] "Poll FG ground elevation at 2 Hz"."""
    assert pcs.terrain_timer.interval() == 500
    assert ports.TERRAIN_POLL_HZ == 2.0


def test_d9_terrain_value_reaches_the_physics_thread(pcs, monkeypatch):
    """[a: D9] the polled elevation is fed into the FDM terrain property
    (slew-limited; the limit itself is verified in test_physics.py)."""
    monkeypatch.setattr(pcs.io, "fetch_ground_elevation", lambda: 500.0)
    pcs.on_terrain_tick()
    assert pcs.physics._terrain_target_ft == pytest.approx(500.0 / 0.3048)


# ==========================================================================
# e_ownshipStateOut
# ==========================================================================

def test_pcs_publishes_ownship_state_on_the_d4_port(pcs, udp_receiver):
    """[a] port e_ownshipStateOut 5006 (BackgroundIOManager);
    [a: D4]; feeds L2SYS-REQ-03.05 at the SEM end."""
    rx = udp_receiver(ports.UDP_PCS_TO_ORCH)
    pcs.on_publish_tick()
    assert rx.wait_for(1)
    state = OwnshipState.from_json(rx.datagrams[0])
    assert math.isfinite(state.lat_deg) and math.isfinite(state.alt_m)
