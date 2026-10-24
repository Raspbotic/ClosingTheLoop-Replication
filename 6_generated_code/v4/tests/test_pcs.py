"""PCS rows. Widgets are obtained from the PilotControlStation application and driven offscreen.
[a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#UserInterfaceManager]"""
import csv
import json
import os
import time

import pytest
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtTest import QTest

from flightsim.common import ports, messages as m
from flightsim.pcs.airfields import SEED
from tests.conftest import send_udp
from tests.pcs_helpers import make_pcs, advance, tick, FakeClock


@pytest.fixture
def env(qapp, tmp_path):
    clock = FakeClock()
    pcs = make_pcs(tmp_path, clock=clock)
    yield pcs, clock
    pcs.shutdown()


def child(pcs, cls, name):
    w = pcs.ui.findChild(cls, name)
    assert w is not None, name
    return w


def airborne(pcs, clock, alt_agl=3000.0, speed=200.0, hdg=90.0):
    ui = pcs.ui
    ui.fields["lat"].setValue(48.5); ui.fields["lon"].setValue(11.6)
    ui.fields["alt"].setValue(SEED["EDDM"].elevation_m + alt_agl); ui.fields["spd"].setValue(speed); ui.fields["hdg"].setValue(hdg)
    child(pcs, QtWidgets.QPushButton, "ssf_set_position").click()
    tick(pcs)
    child(pcs, QtWidgets.QPushButton, "ssf_run").click()
    return advance(pcs, 0.2, clock)


def engine_running(pcs, clock):
    child(pcs, QtWidgets.QPushButton, "ssf_run").click()
    child(pcs, QtWidgets.QPushButton, "btn_engine").click()
    t = advance(pcs, 30, clock)
    assert t["engine_state"] == "RUNNING"
    return t


def test_system_controls_panel_present(env):
    """L2SYS-REQ-01.06: System Controls Panel with ENGINE, PARK BRAKE, AIR BRAKE, LDG GEAR."""
    pcs, _ = env
    panel = child(pcs, QtWidgets.QGroupBox, "system_controls_panel")
    assert panel.title() == "System Controls" and panel.isVisible()
    for n in ("btn_engine", "btn_park_brake", "btn_air_brake", "btn_ldg_gear"):
        assert panel.findChild(QtWidgets.QPushButton, n) is not None


def test_engine_button_starts_and_stops(env):
    """L2SYS-REQ-01.06b: ENGINE toggle-button ON/OFF."""
    pcs, clock = env
    btn = child(pcs, QtWidgets.QPushButton, "btn_engine")
    assert btn.text() == "ENGINE: OFF"
    engine_running(pcs, clock)
    assert btn.text() == "ENGINE: ON"
    btn.click()
    t = advance(pcs, 1, clock)
    assert t["engine_state"] == "OFF" and btn.text() == "ENGINE: OFF"


def test_park_brake_button(env):
    """L2SYS-REQ-01.06c: PARK BRAKE toggle-button ON/OFF (amendment A-1: held at idle thrust)."""
    pcs, clock = env
    engine_running(pcs, clock)
    btn = child(pcs, QtWidgets.QPushButton, "btn_park_brake")
    assert btn.text() == "PRK BRK: ON"
    t = advance(pcs, 3, clock)
    assert t["groundspeed_mps"] < 1.0
    btn.click()
    t = advance(pcs, 10, clock)
    assert not t["park_brake"] and btn.text() == "PRK BRK: OFF" and t["groundspeed_mps"] > 1.0
    btn.click()
    assert advance(pcs, 0.1, clock)["park_brake"]


def test_air_brake_button(env):
    """L2SYS-REQ-01.06d: AIR BRAKE toggle-button ON/OFF."""
    pcs, clock = env
    airborne(pcs, clock)
    btn = child(pcs, QtWidgets.QPushButton, "btn_air_brake")
    btn.click()
    assert advance(pcs, 3, clock)["speedbrake_pos"] > 0.5 and btn.text() == "AIR BRAKE: ON"
    btn.click()
    assert advance(pcs, 3, clock)["speedbrake_pos"] < 0.1


def test_gear_button(env):
    """L2SYS-REQ-01.06e: LDG GEAR toggle-button UP/DOWN; gear-up refused on the ground."""
    pcs, clock = env
    btn = child(pcs, QtWidgets.QPushButton, "btn_ldg_gear")
    btn.click()
    assert advance(pcs, 0.5, clock)["gear_down"] is True
    airborne(pcs, clock)
    btn.click()
    t = advance(pcs, 10, clock)
    assert t["gear_pos"] < 0.1 and btn.text() == "GEAR: UP"
    btn.click()
    assert advance(pcs, 10, clock)["gear_pos"] > 0.9


def test_virtual_controls_drive_when_no_hotas(env):
    """L2SYS-REQ-01.08b: on-screen stick, throttle and pedals usable when the HOTAS is absent."""
    pcs, clock = env
    vh = pcs.ui.vhotas
    stick = vh.stick
    c = stick.rect().center()
    QTest.mousePress(stick, QtCore.Qt.LeftButton, pos=QtCore.QPoint(c.x() + 30, c.y() + 30))
    QTest.mouseMove(stick, QtCore.QPoint(c.x() + 40, c.y() + 40))
    vh.throttle.setValue(40)
    vh.pedals.setValue(-50)
    tick(pcs)
    ctl = pcs.physics.controls
    assert ctl["roll"] > 0 and ctl["pitch"] > 0  # drag down = stick back = pitch + [a: Behaviour B8]
    assert ctl["throttle"] == 0.4 and ctl["yaw"] == -0.5
    QTest.mouseRelease(stick, QtCore.Qt.LeftButton, pos=QtCore.QPoint(c.x() + 40, c.y() + 40))
    tick(pcs)
    assert pcs.physics.controls["roll"] == 0 and pcs.physics.controls["pitch"] == 0


def test_stick_springs_on_release(env):
    """L2SYS-REQ-01.08b / B8: stick and pedals spring to neutral on any mouse release."""
    pcs, _ = env
    vh = pcs.ui.vhotas
    QTest.mousePress(vh.stick, QtCore.Qt.LeftButton, pos=QtCore.QPoint(5, 5))
    assert vh.stick.roll != 0
    QTest.mouseRelease(vh.stick, QtCore.Qt.LeftButton, pos=QtCore.QPoint(5, 5))
    assert (vh.stick.roll, vh.stick.pitch) == (0.0, 0.0)
    vh.pedals.setValue(70)
    QTest.mouseClick(vh.pedals, QtCore.Qt.LeftButton, pos=QtCore.QPoint(3, vh.pedals.height() // 2))  # groove click
    assert vh.pedals.value() == 0


def test_physical_precedence_and_fallback(env):
    """L2SYS-REQ-01.08b / B8: physical HOTAS takes precedence; after the window the on-screen
    controls take over, stick and pedals centred, throttle retained."""
    pcs, clock = env
    vh = pcs.ui.vhotas
    h = m.HotasControls(seq=1, roll=0.3, pitch=-0.2, yaw=0.1, throttle=0.65)
    send_udp(ports.HOTAS_CONTROL_PORT, m.encode(h))
    time.sleep(0.05)
    tick(pcs)
    assert vh.locked and "HOTAS" in vh.stick_box.title() and not vh.throttle.isEnabled()
    ctl = pcs.physics.controls
    assert (ctl["roll"], ctl["pitch"], ctl["yaw"], ctl["throttle"]) == (0.3, -0.2, 0.1, 0.65)
    assert abs(vh.stick.roll - 0.3) < 1e-9 and vh.throttle.value() == 65  # mirrored
    clock.t += 1.1  # precedence window expires
    tick(pcs)
    assert not vh.locked and vh.throttle.isEnabled()
    ctl = pcs.physics.controls
    assert (ctl["roll"], ctl["pitch"], ctl["yaw"]) == (0.0, 0.0, 0.0) and ctl["throttle"] == 0.65


def test_gauges_present_and_move(env):
    """L2SYS-REQ-01.08c (T part): Airspeed, Attitude, Fuel, Heading, RPM, HUD, Attention Getters,
    Message Window exist in the application window and change with the displayed state."""
    pcs, clock = env
    names = ["gauge_airspeed", "gauge_attitude", "gauge_fuel", "gauge_heading", "gauge_rpm", "gauge_hud",
             "attention_strip"]
    ws = {n: child(pcs, QtWidgets.QWidget, n) for n in names}
    assert all(w.isVisible() for w in ws.values())
    assert child(pcs, QtWidgets.QPlainTextEdit, "message_window").isVisible()
    tick(pcs)
    before = (ws["gauge_airspeed"].value, ws["gauge_heading"].heading, ws["gauge_rpm"].value, ws["gauge_fuel"].value)
    engine_running(pcs, clock)
    child(pcs, QtWidgets.QPushButton, "ssf_pause").click(); tick(pcs)
    airborne(pcs, clock, hdg=200.0)
    pcs.ui.vhotas.stick.set_position(0.5, 0.0, emit=True)
    advance(pcs, 2, clock)
    assert ws["gauge_airspeed"].value != before[0] and ws["gauge_heading"].heading != before[1]
    assert ws["gauge_rpm"].value > before[2] and ws["gauge_fuel"].value < before[3]
    assert ws["gauge_attitude"].roll > 5 and ws["gauge_hud"].roll > 5


def test_sim_control_panel_buttons(env):
    """L2SYS-REQ-01.10: Sim Control Panel carries the SSF of 01.10b; menu offers the same."""
    pcs, _ = env
    panel = child(pcs, QtWidgets.QGroupBox, "sim_control_panel")
    texts = {b.text() for b in panel.findChildren(QtWidgets.QPushButton)}
    assert texts == {"RUN SIM", "PAUSE SIM", "RESET SIM", "LAUNCH 3D OTW", "SET POSITION", "Save Snapshot",
                     "Load Snapshot", "FUEL FREEZE", "Telemetry Capture"}
    assert len(pcs.ui.ssf_actions) == 9


def test_run_pause(env):
    """L2SYS-REQ-01.10b RUN SIM / PAUSE SIM (flight-freeze)."""
    pcs, clock = env
    t0 = advance(pcs, 0.5, clock)["sim_time_s"]
    assert t0 == 0.0
    child(pcs, QtWidgets.QPushButton, "ssf_run").click()
    t1 = advance(pcs, 1.0, clock)["sim_time_s"]
    assert abs(t1 - 1.0) < 0.02
    pcs.ui.ssf_actions["pause"].trigger()  # menu path, same gate
    t2 = advance(pcs, 1.0, clock)["sim_time_s"]
    t3 = advance(pcs, 1.0, clock)["sim_time_s"]
    assert t2 == t3


def test_reset_restores_t0(env):
    """L2SYS-REQ-01.10b RESET SIM: initial state t0 (B6 clean slate)."""
    pcs, clock = env
    engine_running(pcs, clock)
    child(pcs, QtWidgets.QPushButton, "btn_park_brake").click()
    pcs.ui.btn_ap.click()
    child(pcs, QtWidgets.QPushButton, "ssf_fuel_freeze").click()
    advance(pcs, 3, clock)
    reset = child(pcs, QtWidgets.QPushButton, "ssf_reset")
    assert not reset.isEnabled()  # freeze-gated
    child(pcs, QtWidgets.QPushButton, "ssf_pause").click(); tick(pcs)
    reset.click()
    t = advance(pcs, 0.2, clock)
    af = SEED["EDDM"]
    assert t["sim_time_s"] == 0.0 and t["engine_state"] == "OFF" and t["park_brake"] and t["gear_down"]
    assert not (t["ap_engaged"] or t["ah_engaged"] or t["at_engaged"] or t["fuel_freeze"])
    assert abs(t["lat_deg"] - af.lat_deg) < 5e-4 and not pcs.ui.btn_ap.isChecked()


def test_set_position(env):
    """L2SYS-REQ-01.10b SET POSITION: Lat, Lon (deg), Altitude (m), Speed (m/s), Heading (deg)."""
    pcs, clock = env
    t = airborne(pcs, clock, alt_agl=2000.0, speed=180.0, hdg=45.0)
    assert abs(t["lat_deg"] - 48.5) < 0.01 and abs(t["lon_deg"] - 11.6) < 0.01
    assert abs(t["alt_m"] - (SEED["EDDM"].elevation_m + 2000)) < 20 and abs(t["speed_mps"] - 180) < 5
    assert abs(t["heading_deg"] - 45) < 2


def test_snapshot_roundtrip_no_alias(env, tmp_path):
    """L2SYS-REQ-01.10b SAVE/LOAD SNAPSHOT: full state restored; no aliasing."""
    pcs, clock = env
    airborne(pcs, clock)
    child(pcs, QtWidgets.QPushButton, "ssf_pause").click(); tick(pcs)
    path = str(tmp_path / "snap.json")
    pcs.ask_save_path = lambda: path
    pcs.ask_open_path = lambda: path
    child(pcs, QtWidgets.QPushButton, "ssf_save").click()
    saved = json.load(open(path))
    ref = pcs.telemetry
    child(pcs, QtWidgets.QPushButton, "ssf_run").click()
    advance(pcs, 5, clock)
    assert json.load(open(path)) == saved  # live mutation does not alter the snapshot
    child(pcs, QtWidgets.QPushButton, "ssf_pause").click(); tick(pcs)
    child(pcs, QtWidgets.QPushButton, "ssf_load").click()
    t = advance(pcs, 0.05, clock)
    for k, tol in (("lat_deg", 1e-5), ("lon_deg", 1e-5), ("alt_m", 1.0), ("speed_mps", 1.0), ("heading_deg", 0.5)):
        assert abs(t[k] - ref[k]) < tol, k
    assert t["engine_state"] == "RUNNING" and abs(t["sim_time_s"] - ref["sim_time_s"]) < 0.05
    pcs.physics.controls["throttle"] = 0.99  # mutate live state after load
    assert json.load(open(path))["physics"]["controls"]["throttle"] != 0.99


def test_fuel_freeze(env):
    """L2SYS-REQ-01.10b FUEL FREEZE: tank contents constant with the engine running."""
    pcs, clock = env
    engine_running(pcs, clock)
    btn = child(pcs, QtWidgets.QPushButton, "ssf_fuel_freeze")
    btn.click()
    f0 = advance(pcs, 0.1, clock)["fuel_lbs"]
    assert abs(advance(pcs, 10, clock)["fuel_lbs"] - f0) < 0.01
    btn.click()
    assert advance(pcs, 10, clock)["fuel_lbs"] < f0 - 0.1


def test_telemetry_capture_header(env, tmp_path):
    """L2SYS-REQ-01.10b TELEMETRY CAPTURE: CSV with the ssfLogTelemetry field list as header."""
    pcs, clock = env
    btn = child(pcs, QtWidgets.QPushButton, "ssf_capture")
    btn.click()
    child(pcs, QtWidgets.QPushButton, "ssf_run").click()
    advance(pcs, 1.0, clock)
    btn.click()
    files = os.listdir(tmp_path / "telemetry")
    assert len(files) == 1
    rows = list(csv.reader(open(tmp_path / "telemetry" / files[0])))
    expected = ("wall_time_iso, sim_time_s, model, lat_deg, lon_deg, alt_m, agl_m, v_speed_mps, speed_mps, "
                "heading_deg, target_heading_deg, pitch_deg, pitch_rate_dps, roll_deg, target_alt_m, "
                "target_speed_mps, aileron_cmd, elevator_cmd, rudder_cmd, throttle_cmd, gear_cmd, trim_pitch, "
                "trim_roll, trim_yaw, ap_engaged, ah_engaged, at_engaged, warning_state").split(", ")
    assert rows[0] == expected and len(expected) == 28
    assert len(rows) >= 5 and all(len(r) == 28 for r in rows)


def test_launch_otw_unconfigured(env):
    """L2SYS-REQ-01.10b LAUNCH 3D OTW with no visual system: reported, nothing raised (B4)."""
    pcs, _ = env
    child(pcs, QtWidgets.QPushButton, "ssf_launch").click()
    assert "NOT CONFIGURED" in pcs.ui.messages.toPlainText()
    assert pcs.otw_process is None


def test_airport_combo_gated_by_freeze(env):
    """L2SYS-REQ-01.04b: airport selection changeable only during flight-freeze."""
    pcs, clock = env
    cmb = child(pcs, QtWidgets.QComboBox, "cmb_airport")
    child(pcs, QtWidgets.QPushButton, "ssf_run").click()
    advance(pcs, 0.2, clock)
    assert not cmb.isEnabled()
    pcs._on_airport(cmb.findData("EDDN"))  # even if forced, refused
    assert advance(pcs, 0.2, clock)["airport"] == "EDDM"
    child(pcs, QtWidgets.QPushButton, "ssf_pause").click(); tick(pcs)
    assert cmb.isEnabled()
    idx = cmb.findData("EDDN")
    cmb.setCurrentIndex(idx); cmb.activated.emit(idx)
    t = advance(pcs, 0.2, clock)
    assert t["airport"] == "EDDN" and abs(t["lat_deg"] - SEED["EDDN"].lat_deg) < 1e-3


def test_nav_panel_controls(env):
    """L2SYS-REQ-01.11: Navigation Controls Panel sets Alt/Spd/Hdg targets and engages AP/AH/AT."""
    pcs, clock = env
    panel = child(pcs, QtWidgets.QGroupBox, "navigation_controls_panel")
    for n in ("field_alt", "field_spd", "field_hdg"):
        assert panel.findChild(QtWidgets.QDoubleSpinBox, n) is not None
    airborne(pcs, clock, alt_agl=2500.0, hdg=100.0)
    for n in ("btn_ap", "btn_ah", "btn_at"):
        child(pcs, QtWidgets.QPushButton, n).click()
    t = advance(pcs, 0.1, clock)
    assert t["ap_engaged"] and t["ah_engaged"] and t["at_engaged"]
    assert abs(t["target_heading_deg"] - 100) < 1  # sync-on-engage [a: Behaviour B9]
    pcs.ui.fields["hdg"].setValue(130.0)
    assert advance(pcs, 0.1, clock)["target_heading_deg"] == 130.0
    child(pcs, QtWidgets.QPushButton, "btn_ap").click()
    assert not advance(pcs, 0.1, clock)["ap_engaged"]


@pytest.mark.slow
def test_altitude_hold_converges(env):
    """L2SYS-REQ-01.11 / B9: AH holds altitude within 30 m over 30 s; AP captures a new heading."""
    pcs, clock = env
    airborne(pcs, clock, alt_agl=3000.0, speed=200.0, hdg=90.0)
    child(pcs, QtWidgets.QPushButton, "btn_ah").click()
    child(pcs, QtWidgets.QPushButton, "btn_at").click()
    child(pcs, QtWidgets.QPushButton, "btn_ap").click()
    t = advance(pcs, 0.1, clock)
    target = t["target_alt_m"]
    pcs.ui.fields["hdg"].setValue(120.0)
    worst = 0.0
    for _ in range(30):
        t = advance(pcs, 1.0, clock)
        worst = max(worst, abs(t["alt_m"] - target))
    assert worst < 30.0
    assert abs(((t["heading_deg"] - 120 + 180) % 360) - 180) < 15  # turning onto the new target


def test_beacon_received_from_6006(env):
    """L2SYS-REQ-02.03: PCS receives threat detections from the Synthetic Environment (port 6006)
    and forwards them to the map."""
    pcs, _ = env
    b = m.RadarBeacon(beacon_id=4, classification="FOE", lat_deg=48.4, lon_deg=11.8, alt_m=500.0,
                      max_range_m=30000.0, detected=True, slant_range_m=10000.0)
    send_udp(ports.PCS_RADAR_IN_PORT, m.encode(b))
    time.sleep(0.05)
    tick(pcs)
    assert pcs.io.beacons[4] == b
    assert pcs.map_window.mapGuiWorker.beacons[4] == b
    assert pcs.ui.lbl_threats.text() == "THREATS: 1/1"
    b.active = False
    send_udp(ports.PCS_RADAR_IN_PORT, m.encode(b))
    time.sleep(0.05)
    tick(pcs)
    assert 4 not in pcs.map_window.mapGuiWorker.beacons


def test_pull_up_flashes(env):
    """L2SYS-REQ-02.04: PULL UP displayed when AGL and vertical speed indicate impact; flashes."""
    pcs, clock = env
    strip = child(pcs, QtWidgets.QWidget, "attention_strip")
    airborne(pcs, clock, alt_agl=600.0, speed=220.0)
    assert strip.shown_text() == ""
    pcs.ui.vhotas.stick.set_position(0.0, -0.6, emit=True)  # push: descend
    for _ in range(40):
        t = advance(pcs, 0.5, clock)
        if t["warning_state"]:
            break
    assert t["warning_state"]
    seen = set()
    for _ in range(12):
        QTest.qWait(60)
        seen.add(strip.shown_text())
    assert seen == {"PULL UP", ""}
    pcs.ui.vhotas.stick.set_position(0.0, 0.0, emit=True)
    child(pcs, QtWidgets.QPushButton, "ssf_pause").click(); tick(pcs)
    child(pcs, QtWidgets.QPushButton, "ssf_reset").click()
    advance(pcs, 0.1, clock)
    assert strip.shown_text() == ""


def _rolling(pcs, clock):
    engine_running(pcs, clock)
    child(pcs, QtWidgets.QPushButton, "btn_park_brake").click()
    pcs.ui.vhotas.throttle.setValue(40)
    t = advance(pcs, 8, clock)
    pcs.ui.vhotas.throttle.setValue(0)
    t = advance(pcs, 0.5, clock)
    assert t["groundspeed_mps"] > 5 and t["engine_state"] == "RUNNING"
    return t


def test_gui_pedal_brake_stops_aircraft(env):
    """L2SYS-REQ-01.08b (field defect D-4): the on-screen PEDAL BRAKE decelerates the rolling
    aircraft to a stop with the engine running at idle."""
    pcs, clock = env
    _rolling(pcs, clock)
    btn = child(pcs, QtWidgets.QPushButton, "btn_pedal_brake")
    QTest.mousePress(btn, QtCore.Qt.LeftButton)
    t = advance(pcs, 10, clock)
    QTest.mouseRelease(btn, QtCore.Qt.LeftButton)
    assert t["groundspeed_mps"] < 0.5 and t["engine_state"] == "RUNNING"


def test_gui_pedal_brake_with_physical_hotas(env):
    """L2SYS-REQ-01.08b (field defect D-4): PEDAL BRAKE also works while the physical HOTAS
    holds precedence (its own brake button not pressed)."""
    pcs, clock = env
    _rolling(pcs, clock)
    btn = child(pcs, QtWidgets.QPushButton, "btn_pedal_brake")
    QTest.mousePress(btn, QtCore.Qt.LeftButton)
    seq = 0
    for _ in range(100):  # 10 s, a HOTAS datagram every 0.1 s
        seq += 1
        send_udp(ports.HOTAS_CONTROL_PORT, m.encode(m.HotasControls(seq=seq, roll=0.0, pitch=0.0, yaw=0.0, throttle=0.0)))
        time.sleep(0.002)
        t = advance(pcs, 0.1, clock)
    QTest.mouseRelease(btn, QtCore.Qt.LeftButton)
    assert pcs.ui.vhotas.locked  # physical HOTAS had precedence throughout
    assert t["groundspeed_mps"] < 0.5 and t["engine_state"] == "RUNNING"
