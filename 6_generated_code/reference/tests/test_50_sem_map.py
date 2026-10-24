"""SEM detection math + protocol triggers; map coordinate math + rendering.
Traces: fsim_TA_SEM.sysml, fsim_TA_PCS_ThreadMapWindow.sysml,
L2SYS-REQ-02.01/02.2/02.3/03.1, req_scenarioManagement."""
import os, socket, time, pytest
from flightsim.common import ports
from flightsim.common.messages import OwnshipState, RadarBeacon, decode


def test_slant_range_math():
    from flightsim.run_sem import slant_range_m
    d = slant_range_m(45.0, 9.0, 0, 45.18, 9.0, 0)
    assert 19500 < d < 20500, f"0.18deg lat should be ~20km, got {d:.0f}"
    assert abs(slant_range_m(45.0, 9.0, 0, 45.0, 9.0, 3000) - 3000) < 1


def test_slippy_map_math():
    from flightsim.pcs.map_window import deg2num, meters_per_pixel
    assert tuple(map(int, deg2num(0.0, 0.0, 1))) == (1, 1)
    assert tuple(map(int, deg2num(45.45, 9.28, 11))) == (1076, 733)
    assert 50 < meters_per_pixel(45.45, 11) < 60


class TestSemTriggers:
    @pytest.fixture
    def sem(self, qapp, udp_rx):
        from flightsim.run_sem import SemWindow
        rx = udp_rx(ports.UDP_SEM_RADAR_DETECTIONS)  # play orchestrator
        win = SemWindow()
        win.show()
        yield win, rx
        win.beacons.clear()   # avoid exit tombstone noise
        win.close()

    @staticmethod
    def drain(rx):
        out = []
        try:
            while True:
                out.append(decode(rx.recvfrom(65535)[0]))
        except OSError:
            pass
        return out

    def test_ownship_rx_and_all_triggers(self, qapp, sem):
        from conftest import process_events
        win, rx = sem
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # connectToSim: ownship arrives on 5007
        tx.sendto(OwnshipState(seq=1, lat_deg=45.45, lon_deg=9.28,
                               alt_m=2000).to_json(),
                  (ports.LOCALHOST, ports.UDP_SEM_OWNSHIP_STATE))
        process_events(qapp, 0.3)
        assert win.ownship is not None, "SEM must receive ownship on 5007"
        # trigger 1: inject (far away -> not detected)
        b = RadarBeacon(beacon_id="S1", classification="FOE",
                        lat_deg=46.45, lon_deg=9.28, max_range_m=25000)
        win.beacons[b.beacon_id] = b
        win._tx_beacon(b)
        process_events(qapp, 0.2)
        msgs = self.drain(rx)
        assert any(m.beacon_id == "S1" and m.active and not m.detected
                   for m in msgs), "inject event must be transmitted"
        # trigger 3: fly into envelope -> detection flip transmitted
        tx.sendto(OwnshipState(seq=2, lat_deg=46.40, lon_deg=9.28,
                               alt_m=2000).to_json(),
                  (ports.LOCALHOST, ports.UDP_SEM_OWNSHIP_STATE))
        process_events(qapp, 0.4)
        msgs = self.drain(rx)
        assert any(m.beacon_id == "S1" and m.detected for m in msgs), \
            "detection flip must be transmitted"
        # trigger 2: remove -> tombstone
        bb = win.beacons.pop("S1"); bb.active = False; win._tx_beacon(bb)
        process_events(qapp, 0.2)
        msgs = self.drain(rx)
        assert any(m.beacon_id == "S1" and not m.active for m in msgs), \
            "tombstone must be transmitted"
        tx.close()


def test_map_beacon_table_and_rendering(qapp, tmp_path):
    from conftest import process_events
    from PyQt5.QtGui import QImage, QColor
    from flightsim.pcs.map_window import MapWindow, deg2num
    # pre-populate offline tile cache
    tm = OwnshipState(lat_deg=45.45, lon_deg=9.28, heading_deg=45)
    cx, cy = deg2num(tm.lat_deg, tm.lon_deg, 11)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            p = tmp_path / "11" / str(int(cx) + dx)
            p.mkdir(parents=True, exist_ok=True)
            img = QImage(256, 256, QImage.Format_RGB32)
            img.fill(QColor(100, 140, 100))
            img.save(str(p / f"{int(cy)+dy}.png"))
    win = MapWindow(tile_cache_path=str(tmp_path))
    win.resize(500, 540); win.show()
    win.on_ownship(tm)
    win.on_beacon(RadarBeacon(beacon_id="R1", classification="FOE",
                              lat_deg=45.50, lon_deg=9.35,
                              max_range_m=15000, detected=True))
    process_events(qapp, 1.0)
    assert len(win.map_widget._tiles) >= 3, "cached tiles must render"
    img = win.map_widget.grab().toImage()
    red = any(QColor(img.pixel(x, y)).red() > 180
              and QColor(img.pixel(x, y)).green() < 100
              for x in range(0, img.width(), 8)
              for y in range(0, img.height(), 8))
    assert red, "FOE ring/symbol must paint red pixels (L2SYS-REQ-02.03)"
    win.on_beacon(RadarBeacon(beacon_id="R1", active=False))
    assert "R1" not in win.map_widget.beacons, "tombstone must clear map"
    win.close()


class TestRadarEquation:
    """calcRadarEquation [a: L2SYS-REQ-03.05], emitter params [03.2],
    RCS scenario parameter [03.3]."""

    def test_hand_computed_reference(self):
        """Pt=100kW, G=35dBi, f=10GHz, Pmin=-90dBm, RCS=5m2 -> ~38.8 km
        (independently hand-computed: R^4 = Pt*G^2*lam^2*sigma /
        ((4pi)^3 * 1e-12))."""
        import math
        from flightsim.run_sem import radar_max_range_m
        r = radar_max_range_m(100e3, 35.0, 10e9, -90.0, 5.0)
        g = 10 ** 3.5
        lam = 299792458.0 / 10e9
        expected = ((100e3 * g * g * lam * lam * 5.0)
                    / ((4 * math.pi) ** 3 * 1e-12)) ** 0.25
        assert abs(r - expected) < 1.0
        assert 35000 < r < 43000, f"~39 km expected, got {r/1000:.1f} km"

    def test_scaling_laws(self):
        """R ~ sigma^(1/4); R ~ Pt^(1/4); Pmin +12dB -> R halves...
        actually -12dB sensitivity: R * 10^(-12/40) ~= 0.5."""
        from flightsim.run_sem import radar_max_range_m
        r0 = radar_max_range_m(100e3, 35.0, 10e9, -90.0, 5.0)
        assert abs(radar_max_range_m(100e3, 35.0, 10e9, -90.0, 80.0)
                   / r0 - 2.0) < 0.01, "16x RCS must double R"
        assert abs(radar_max_range_m(1600e3, 35.0, 10e9, -90.0, 5.0)
                   / r0 - 2.0) < 0.01, "16x power must double R"
        assert abs(radar_max_range_m(100e3, 35.0, 10e9, -78.0, 5.0)
                   / r0 - 0.5) < 0.01, "+12 dB Pmin must halve R"

    def test_rcs_change_recomputes_and_retransmits(self, qapp, udp_rx):
        """[a: 03.3] RCS change -> all beacons recomputed + tx'd."""
        from conftest import process_events
        from flightsim.run_sem import SemWindow, EmitterParams
        from flightsim.common import ports
        from flightsim.common.messages import RadarBeacon, decode
        rx = udp_rx(ports.UDP_SEM_RADAR_DETECTIONS)
        win = SemWindow(); win.show()
        try:
            b = RadarBeacon(beacon_id="RQ1", classification="FOE",
                            lat_deg=45.6, lon_deg=9.28, slant_range_m=20000)
            win.beacons[b.beacon_id] = b
            win.emitters[b.beacon_id] = EmitterParams()   # defaults
            win._recompute_beacon(b)
            r_at_5 = b.max_range_m
            assert 35000 < r_at_5 < 43000
            assert b.detected, "20 km inside ~39 km envelope"
            win.sp_rcs.setValue(5.0 / 16.0)   # RCS/16 -> R_max/2 (~19.4km)
            process_events(qapp, 0.2)
            assert abs(b.max_range_m / r_at_5 - 0.5) < 0.01
            assert not b.detected, "20 km now outside ~19.4 km envelope"
            msgs = []
            try:
                while True:
                    msgs.append(decode(rx.recvfrom(65535)[0]))
            except OSError:
                pass
            assert any(m.beacon_id == "RQ1" and not m.detected
                       and abs(m.max_range_m / r_at_5 - 0.5) < 0.01
                       for m in msgs), "recomputed beacon must be tx'd"
        finally:
            win.beacons.clear()
            win.close()