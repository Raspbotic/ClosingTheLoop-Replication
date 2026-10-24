"""Tactical2DMap rows 02.01, 02.03b. The map window is obtained from the PCS application.
[a: fsim_TA_PCS_ThreadMapWindow.sysml#ThreadMapWindow]"""
import math
import time

import pytest
from PyQt5 import QtCore, QtGui, QtWidgets

from flightsim.common import ports, messages as m
from flightsim.pcs import map_window as mw
from tests.conftest import send_udp
from tests.pcs_helpers import make_pcs, tick


def _png_bytes(color=QtGui.QColor(200, 200, 255)):
    img = QtGui.QImage(256, 256, QtGui.QImage.Format_RGB32)
    img.fill(color)
    buf = QtCore.QBuffer(); buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def test_tile_math():
    """L2SYS-REQ-02.01: tile coordinates for the ownship GPS position, hand-computed."""
    assert mw.latlon_to_tile_xy(0.0, 0.0, 1) == (1.0, 1.0)
    lat, lon, z = 48.34067, 11.751, 12
    n = 2 ** z
    x_ref = (lon + 180) / 360 * n                                           # 2181.70...
    y_ref = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    x, y = mw.latlon_to_tile_xy(lat, lon, z)
    assert abs(x - x_ref) < 1e-9 and abs(y - y_ref) < 1e-9
    assert mw.tile_url(12, 2181, 1418) == "https://tile.openstreetmap.org/12/2181/1418.png"


def _pump(pred, timeout=3.0):
    app = QtWidgets.QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_tiles_requested_for_ownship(qapp, tmp_path):
    """L2SYS-REQ-02.01: tiles for the ownship position fetched from the tile server and cached;
    the cached tile is used without network on the next start."""
    urls = []
    png = _png_bytes()
    def fetch(url):
        urls.append(url)
        return png
    pcs = make_pcs(tmp_path, fetch=fetch)
    try:
        tick(pcs)
        pcs.map_window.i_OwnStateIn(pcs.telemetry)
        t = pcs.telemetry
        x, y = mw.latlon_to_tile_xy(t["lat_deg"], t["lon_deg"], mw.ZOOM_DEFAULT)
        own_url = mw.tile_url(mw.ZOOM_DEFAULT, int(x), int(y))
        assert _pump(lambda: (mw.ZOOM_DEFAULT, int(x), int(y)) in pcs.map_window.mapGuiWorker.tiles)
        assert own_url in urls and all(u.startswith("https://tile.openstreetmap.org/") for u in urls)
    finally:
        pcs.shutdown()
    def offline(url):
        raise OSError("no network")
    pcs2 = make_pcs(tmp_path, fetch=offline)
    try:
        tick(pcs2)
        pcs2.map_window.i_OwnStateIn(pcs2.telemetry)
        assert _pump(lambda: (mw.ZOOM_DEFAULT, int(x), int(y)) in pcs2.map_window.mapGuiWorker.tiles)
    finally:
        pcs2.shutdown()


def test_foe_ring_red_scaled(qapp, tmp_path):
    """L2SYS-REQ-02.03b: threat detections displayed as scaled, colour-coded rings."""
    pcs = make_pcs(tmp_path)
    try:
        tick(pcs)
        t = pcs.telemetry
        foe = m.RadarBeacon(beacon_id=1, classification="FOE", lat_deg=t["lat_deg"], lon_deg=t["lon_deg"],
                            alt_m=t["alt_m"], max_range_m=2000.0, detected=True, slant_range_m=0.0)
        friend = m.RadarBeacon(beacon_id=2, classification="FRIEND", lat_deg=t["lat_deg"], lon_deg=t["lon_deg"],
                               alt_m=t["alt_m"], max_range_m=1000.0)
        send_udp(ports.PCS_RADAR_IN_PORT, m.encode(foe)); send_udp(ports.PCS_RADAR_IN_PORT, m.encode(friend))
        time.sleep(0.05)
        tick(pcs)
        pcs.map_window.i_OwnStateIn(t)
        w = pcs.map_window.mapGuiWorker
        w.resize(500, 500)
        img = w.grab().toImage()
        cx, cy = w.width() / 2, w.height() / 2
        # hand computation: metres per pixel at latitude and zoom (Web Mercator, 256 px tiles)
        mpp = 40075016.686 * math.cos(math.radians(t["lat_deg"])) / (256 * 2 ** w.zoom)
        for rng, pred in ((2000.0, lambda c: c.red() > 200 and c.green() < 90 and c.blue() < 90),
                          (1000.0, lambda c: c.green() > 180 and c.red() < 90)):
            r = rng / mpp
            hits = 0
            for ang in (0, 45, 90, 135, 180, 225, 270, 315):
                best = False
                for dr in range(-3, 4):
                    px = int(round(cx + (r + dr) * math.cos(math.radians(ang))))
                    py = int(round(cy + (r + dr) * math.sin(math.radians(ang))))
                    best = best or pred(QtGui.QColor(img.pixel(px, py)))
                hits += best
            assert hits >= 7, (rng, hits)
    finally:
        pcs.shutdown()


def test_beacons_fixed_on_ground_and_in_view(qapp, tmp_path):
    """L2SYS-REQ-02.03b: a ring stays at the beacon's ground position while the ownship moves,
    and the map zooms so the whole ring is visible (a 38.8 km ring 5 km from the ownship)."""
    pcs = make_pcs(tmp_path)
    try:
        w = pcs.map_window.mapGuiWorker
        w.resize(320, 480)
        own = dict(lat_deg=48.30, lon_deg=11.60, heading_deg=90.0)
        w.set_ownship(own)
        b = m.RadarBeacon(beacon_id=1, classification="FOE", lat_deg=48.345, lon_deg=11.6027, alt_m=450.0,
                          max_range_m=38800.0, detected=True, slant_range_m=5000.0)
        w.set_beacons({1: b})
        mpp = mw.meters_per_pixel(48.30, w.zoom)
        assert (5000 + 38800) / mpp <= 160          # ring fits the 320 px width
        p0 = w.geo_to_px(b.lat_deg, b.lon_deg)
        w.set_ownship(dict(own, lon_deg=11.70))      # ownship flies ~7.4 km east
        p1 = w.geo_to_px(b.lat_deg, b.lon_deg)
        # beacon moves WEST on screen by the ownship displacement: fixed on the ground
        dx_px = (11.70 - 11.60) * 111320.0 * math.cos(math.radians(48.30)) / mpp
        assert abs((p0[0] - p1[0]) - dx_px) < 2.0 and abs(p0[1] - p1[1]) < 1.0
        assert b.lat_deg == 48.345 and b.lon_deg == 11.6027
        z = w.zoom
        w.set_beacons({1: b})                        # refresh of a known beacon: zoom untouched
        assert w.zoom == z
    finally:
        pcs.shutdown()
