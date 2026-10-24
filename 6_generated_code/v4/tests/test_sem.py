"""SEM rows: 03.02, 03.03, 03.04, 03.05, B2. Widgets obtained from the SEM application."""
import time
import pytest

from flightsim.common import ports, messages as m
from flightsim.sem.run_sem import SemApp, SemCore
from flightsim.sem import radar
from tests.conftest import send_udp


@pytest.fixture
def sem(qapp):
    s = SemApp(show=True)
    s.window.btn_connect.click()
    assert s.core.connected and s.window.btn_connect.text() == "DISCONNECT"
    yield s
    s.shutdown()


def _beacons(data):
    return [x for x in (m.decode(d) for d in data) if isinstance(x, m.RadarBeacon)]


def test_spawn_classify_via_gui(sem, udp_receiver, qapp):
    """L2SYS-REQ-03.02: GUI spawns and classifies FRIEND/FOE/UNKNOWN emitters with defined max ranges."""
    rx = udp_receiver(ports.SEM_RADAR_OUT_PORT)
    w = sem.window
    w.chk_eq.setChecked(False)
    w.sp_maxr.setValue(12345)
    for t in ("EM_FRIEND", "EM_FOE", "EM_UNKNOWN"):
        w.cmb_type.setCurrentText(t)
        w.btn_add.click()
    got = _beacons(rx.collect(0.2))
    assert [b.classification for b in got] == ["FRIEND", "FOE", "UNKNOWN"]
    assert all(b.max_range_m == 12345 for b in got)
    assert w.lst.count() == 3


def test_emitter_params_editable(sem, qapp):
    """L2SYS-REQ-03.03: Pt, G, f, Pmin set and adjusted per beacon from the GUI."""
    w = sem.window
    w.btn_add.click()
    w.btn_add.click()
    b1 = sem.core.beacons[1]
    r0 = b1.max_range_m
    w.lst.setCurrentRow(0)
    w.sp_pt.setValue(200e3)
    w.btn_apply.click()
    assert abs(b1.max_range_m / r0 - 2 ** 0.25) < 1e-6
    for spin, val, attr in ((w.sp_g, 38.0, "g_dbi"), (w.sp_f, 9.0, "f_hz"), (w.sp_pmin, -95.0, "pmin_dbm")):
        spin.setValue(val); w.btn_apply.click()
        assert getattr(b1, attr) == (val * 1e9 if attr == "f_hz" else val)
    assert sem.core.beacons[2].pt_w == radar.DEFAULT_PT_W  # per beacon


def test_ownship_rcs_recomputes_all(sem, udp_receiver, qapp):
    """L2SYS-REQ-03.04 / B10: ownship RCS scenario control recomputes and retransmits every beacon."""
    w = sem.window
    w.btn_add.click(); w.btn_add.click()
    rx = udp_receiver(ports.SEM_RADAR_OUT_PORT)
    r = [b.max_range_m for b in sem.core.beacons.values()]
    w.sp_rcs.setValue(80.0)  # 16x of default 5 m^2
    got = _beacons(rx.collect(0.2))
    assert {b.beacon_id for b in got} == {1, 2}
    for b, r0 in zip(sem.core.beacons.values(), r):
        assert abs(b.max_range_m / r0 - 2.0) < 1e-9


def test_detection_from_ownship(sem, udp_receiver, qapp):
    """L2SYS-REQ-03.05: detection = slant range <= R_max evaluated against received ownship state."""
    w = sem.window
    lat, lon, alt = sem.core.reference_position()
    w.sp_range.setValue(30000); w.sp_theta.setValue(90); w.btn_add.click()   # inside ~38.8 km
    w.sp_range.setValue(50000); w.btn_add.click()                            # outside
    rx = udp_receiver(ports.SEM_RADAR_OUT_PORT)
    own = m.OwnshipState(seq=1, sim_time_s=0, lat_deg=lat, lon_deg=lon, alt_m=alt, agl_m=0, v_speed_mps=0,
                         speed_mps=0, heading_deg=0, pitch_deg=0, roll_deg=0)
    send_udp(ports.SEM_OWNSHIP_IN_PORT, m.encode(own))
    time.sleep(0.05)
    w.tick()
    b1, b2 = sem.core.beacons[1], sem.core.beacons[2]
    assert b1.detected and not b2.detected
    assert abs(b1.slant_range_m - 30000) < 5
    got = _beacons(rx.collect(0.2))
    assert [b.beacon_id for b in got] == [1]  # only the changed beacon [a: Behaviour B2]
    assert sem.core.status() == "Connected"


def test_refresh_and_tombstone():
    """C-B2: background refresh every 2.0 s retransmits live beacons; removal sends a tombstone."""
    now = [0.0]
    core = SemCore(clock=lambda: now[0])
    core.connect()  # refresh runs only while connected
    sent = []
    core._send = lambda b, active=True: sent.append((b.beacon_id, active))
    core.add_beacon("FOE", 1000, 0)
    core.add_beacon("FRIEND", 1000, 90)
    sent.clear()
    now[0] = 1.9; core.poll()
    assert sent == []
    now[0] = 2.0; core.poll()
    assert sorted(sent) == [(1, True), (2, True)]
    sent.clear()
    core.remove_beacon(1)
    assert sent == [(1, False)]
    core.close()


def test_connect_button_toggles_federation(qapp, udp_receiver):
    """fsim_TA_SEM.sysml#threadMainGui::connectToSim: no traffic before CONNECT; CONNECT publishes
    the defined beacons; DISCONNECT tombstones them and releases the port; status follows."""
    rx = udp_receiver(ports.SEM_RADAR_OUT_PORT)
    s = SemApp(show=True)
    try:
        w = s.window
        assert not s.core.connected and w.btn_connect.text() == "CONNECT TO SIM"
        w.tick()
        assert w.lbl_status.text() == "Status: Disconnected"
        w.btn_add.click()                                   # defined offline
        assert rx.collect(0.2) == []
        w.btn_connect.click()
        got = _beacons(rx.collect(0.2))
        assert [(b.beacon_id, b.active) for b in got] == [(1, True)]
        assert w.btn_connect.text() == "DISCONNECT" and w.lbl_status.text() == "Status: Waiting for PCS"
        w.btn_connect.click()
        got = _beacons(rx.collect(0.2))
        assert [(b.beacon_id, b.active) for b in got] == [(1, False)]
        assert not s.core.connected and w.lbl_status.text() == "Status: Disconnected"
        send_udp(ports.SEM_OWNSHIP_IN_PORT, b"x")  # port released: nobody bound, no error
        w.tick()
        assert rx.collect(0.2) == []
    finally:
        s.shutdown()
