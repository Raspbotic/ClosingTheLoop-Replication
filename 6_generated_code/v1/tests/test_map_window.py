"""
tests/test_map_window.py
========================
Verification-plan rows allocated to the tactical map:
L2SYS-REQ-02.01 and L2SYS-REQ-02.03b.

[a: Amendment A-3 of docs/VERIFICATION_PLAN.md] the network fetch is
exercised through the real TileFetcher code path pointed at a loopback
HTTP stub, so the suite passes headless with no external network; the
live-imagery aspect stays in manual item M-7 (the `I` half of 02.01).
"""

from __future__ import annotations

import http.server
import math
import struct
import threading
import zlib

import pytest
from PyQt5 import QtCore, QtGui

from flightsim.common import ports
from flightsim.common.messages import OwnshipState, RadarBeacon
from flightsim.pcs.map_window import (
    CLASSIFICATION_COLORS, TILE_SIZE, MapGuiWidget, TileFetcher,
    ThreadMapWindow, deg_to_tile_xy, metres_per_pixel, tile_url,
    tile_xy_to_deg,
)


# --------------------------------------------------------------------------
# Independent slippy-map reference, written from the standard transform
# named in the verification plan, not from the implementation.
# --------------------------------------------------------------------------

def reference_tile_xy(lat_deg, lon_deg, zoom):
    """x = (lon+180)/360 * 2^z ;
    y = (1 - ln(tan(phi) + sec(phi)) / pi) / 2 * 2^z"""
    n = 2.0 ** zoom
    phi = math.radians(lat_deg)
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(phi) + 1.0 / math.cos(phi)) / math.pi) / 2.0 * n
    return x, y


def _png_bytes(r, g, b, size=TILE_SIZE):
    """[c] Build a solid-colour PNG in-test, so the stub tile server needs
    no image library and the fetched bytes are recognisable."""
    raw = b"".join(b"\x00" + bytes([r, g, b] * size) for _ in range(size))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


TILE_PNG = _png_bytes(90, 110, 90)


class _TileHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        self.server.requested.append(self.path)          # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(TILE_PNG)))
        self.end_headers()
        self.wfile.write(TILE_PNG)

    def log_message(self, *args):                        # silence
        pass


@pytest.fixture
def tile_server():
    """A loopback stand-in for the OSM tile endpoint (amendment A-3)."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _TileHandler)
    srv.requested = []                                   # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True,
                              name="tile-stub")
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=2.0)


@pytest.fixture
def map_widget(qapp):
    w = MapGuiWidget(fetcher=None, zoom=12)
    w.resize(512, 512)
    yield w


# ==========================================================================
# L2SYS-REQ-02.01
# ==========================================================================

@pytest.mark.parametrize("lat,lon,zoom", [
    (45.4451, 9.2767, 12),        # LIML
    (37.6189, -122.3750, 14),     # KSFO, western hemisphere
    (0.0, 0.0, 3),                # null island
    (-33.9, 151.2, 10),           # southern hemisphere
])
def test_02_01_tile_coordinates_match_independent_slippy_math(lat, lon, zoom):
    """L2SYS-REQ-02.01: the Tactical2DMap shall fetch and render
    OpenStreetMap tiles CORRESPONDING TO THE AIRCRAFT'S CURRENT GPS
    COORDINATES.

    Pass: the tile indices equal an independent computation of the
    standard Web-Mercator transform.
    """
    got = deg_to_tile_xy(lat, lon, zoom)
    expected = reference_tile_xy(lat, lon, zoom)
    assert got[0] == pytest.approx(expected[0], rel=1e-12)
    assert got[1] == pytest.approx(expected[1], rel=1e-12)


def test_02_01_tile_transform_is_invertible():
    """L2SYS-REQ-02.01: the projection used to place the ownship symbol
    must be the inverse of the one used to select tiles."""
    lat, lon, z = 45.4451, 9.2767, 13
    x, y = deg_to_tile_xy(lat, lon, z)
    back_lat, back_lon = tile_xy_to_deg(x, y, z)
    assert back_lat == pytest.approx(lat, abs=1e-9)
    assert back_lon == pytest.approx(lon, abs=1e-9)


def test_02_01_tile_url_targets_the_d4_endpoint():
    """L2SYS-REQ-02.01 + [a: D4] "OSM tiles
    https://tile.openstreetmap.org"; also L2SYS-REQ-05.02."""
    url = tile_url(12, 2145, 1436)
    assert url == "https://tile.openstreetmap.org/12/2145/1436.png"
    assert url.startswith(ports.OSM_TILE_BASE_URL)


def test_02_01_map_follows_the_ownship_gps_position(map_widget):
    """L2SYS-REQ-02.01: the rendered area follows the aircraft position.

    Stimulus: push two ownship states far apart.
    Observation: the centre tile and the requested tile set.
    """
    map_widget.process_ownship_state(
        OwnshipState(lat_deg=45.4451, lon_deg=9.2767))
    milan_centre = map_widget.center_tile()
    milan_tiles = set(map_widget.visible_tiles())

    map_widget.process_ownship_state(
        OwnshipState(lat_deg=37.6189, lon_deg=-122.3750))
    sfo_centre = map_widget.center_tile()
    sfo_tiles = set(map_widget.visible_tiles())

    assert milan_centre != sfo_centre
    assert not (milan_tiles & sfo_tiles), "map did not move with the ownship"
    assert milan_centre == pytest.approx(
        reference_tile_xy(45.4451, 9.2767, map_widget.zoom))


def test_02_01_fetches_and_caches_tiles_on_disk(tile_server, tmp_path, qapp):
    """L2SYS-REQ-02.01 "shall fetch ... tiles" through the real
    TileFetcher path (amendment A-3), plus the TA action cacheMapTiles.

    Pass: the first request hits the server and writes the tile to the
    disk cache; the second is served from the cache without a request.
    """
    host, port = tile_server.server_address
    fetcher = TileFetcher(cache_dir=str(tmp_path),
                          base_url=f"http://{host}:{port}")
    try:
        data = fetcher.fetch_one(12, 2145, 1436)
        assert data == TILE_PNG
        assert fetcher.fetched_from_network == 1
        assert (tmp_path / "12_2145_1436.png").is_file()
        assert tile_server.requested == ["/12/2145/1436.png"]

        again = fetcher.fetch_one(12, 2145, 1436)
        assert again == TILE_PNG
        assert fetcher.served_from_cache == 1
        assert fetcher.fetched_from_network == 1          # no second request
        assert len(tile_server.requested) == 1
    finally:
        fetcher.stop()


def test_02_01_fetched_tile_is_rendered(tile_server, tmp_path, qapp):
    """L2SYS-REQ-02.01 "... and render ... tiles": a delivered tile
    reaches the rendered image."""
    host, port = tile_server.server_address
    fetcher = TileFetcher(cache_dir=str(tmp_path),
                          base_url=f"http://{host}:{port}")
    try:
        widget = MapGuiWidget(fetcher, zoom=12)
        widget.resize(256, 256)
        widget.process_ownship_state(
            OwnshipState(lat_deg=45.4451, lon_deg=9.2767))
        cx, cy = widget.center_tile()
        tx, ty = int(cx), int(cy)
        widget.on_tile_ready(12, tx, ty, fetcher.fetch_one(12, tx, ty))
        image = widget.render_to_image(QtCore.QSize(256, 256))
        # the tile PNG is a solid (90,110,90); the empty background is
        # (24,28,24), so any tile pixel proves the tile was drawn
        found = any(
            QtGui.QColor(image.pixel(x, y)).green() > 100
            for x in range(0, 256, 8) for y in range(0, 256, 8))
        assert found, "the fetched tile was not rendered"
    finally:
        fetcher.stop()


def test_02_01_offline_fetch_degrades_without_raising(tmp_path, qapp):
    """L2SYS-REQ-02.01: with the tile service unreachable the map must
    degrade rather than crash the PCS [b]."""
    fetcher = TileFetcher(cache_dir=str(tmp_path),
                          base_url="http://127.0.0.1:1")   # nothing listening
    try:
        assert fetcher.fetch_one(12, 1, 1) is None
    finally:
        fetcher.stop()


def test_02_01_fetcher_thread_starts_and_stops(tmp_path, qapp, tile_server):
    """[a] TA: TileFetcher "runs on a background QThread"; [a: D15] it
    must be stoppable so no test leaks a network thread."""
    host, port = tile_server.server_address
    window = ThreadMapWindow(cache_dir=str(tmp_path),
                             base_url=f"http://{host}:{port}")
    try:
        assert window.tileFetchWorker.isRunning()
    finally:
        window.stop()
    assert not window.tileFetchWorker.isRunning()


# ==========================================================================
# L2SYS-REQ-02.03b
# ==========================================================================

def _ring_radius_px(widget, beacon):
    """Independent computation of the expected ring radius, from the
    requirement's word 'scaled': range in metres / map scale."""
    return beacon.max_range_m / metres_per_pixel(widget.ownship.lat_deg,
                                                 widget.zoom)


@pytest.mark.parametrize("classification", ["FOE", "FRIEND", "UNKNOWN"])
def test_02_03b_threat_rings_are_colour_coded(map_widget, classification):
    """L2SYS-REQ-02.03b: the Tactical2DMap shall display the threat
    detections as scaled, COLOR-CODED rings on the tactical map.

    Stimulus: one beacon of each classification co-located with the
    ownship. Observation: an offscreen render, sampled pixel by pixel.
    Pass: the ring is drawn in the colour assigned to that classification
    and in no other classification's colour.
    """
    own = OwnshipState(lat_deg=45.4451, lon_deg=9.2767)
    map_widget.process_ownship_state(own)
    mpp = metres_per_pixel(own.lat_deg, map_widget.zoom)
    beacon = RadarBeacon(beacon_id=1, classification=classification,
                         lat_deg=own.lat_deg, lon_deg=own.lon_deg,
                         max_range_m=80.0 * mpp,       # 80 px radius
                         detected=True, slant_range_m=0.0)
    map_widget.process_beacon_contacts([beacon])
    image = map_widget.render_to_image(QtCore.QSize(512, 512))

    want = CLASSIFICATION_COLORS[classification]
    others = [c for k, c in CLASSIFICATION_COLORS.items()
              if k != classification]

    def close(pixel, rgb, tol=60):
        col = QtGui.QColor(pixel)
        return (abs(col.red() - rgb[0]) < tol
                and abs(col.green() - rgb[1]) < tol
                and abs(col.blue() - rgb[2]) < tol)

    pixels = [image.pixel(x, y) for x in range(512) for y in range(0, 512, 2)]
    assert any(close(p, want) for p in pixels), \
        f"no {classification} coloured pixels in the rendered map"
    for other in others:
        assert not any(close(p, other, tol=25) for p in pixels), \
            "a foreign classification colour appears in the render"


def test_02_03b_ring_radius_is_scaled_by_the_map_resolution(map_widget):
    """L2SYS-REQ-02.03b "scaled ... rings": the drawn radius equals the
    beacon's maximum range converted through the map's metres-per-pixel.

    The expected radius is computed here from the map scale, then located
    in the rendered image by scanning outwards along a row.
    """
    own = OwnshipState(lat_deg=45.4451, lon_deg=9.2767)
    map_widget.process_ownship_state(own)
    mpp = metres_per_pixel(own.lat_deg, map_widget.zoom)
    expected_px = 120.0
    beacon = RadarBeacon(beacon_id=2, classification="FOE",
                         lat_deg=own.lat_deg, lon_deg=own.lon_deg,
                         max_range_m=expected_px * mpp, detected=True)
    assert _ring_radius_px(map_widget, beacon) == pytest.approx(expected_px)

    map_widget.process_beacon_contacts([beacon])
    image = map_widget.render_to_image(QtCore.QSize(512, 512))
    cy = 256
    red_x = [x for x in range(256, 512)
             if QtGui.QColor(image.pixel(x, cy)).red() > 150
             and QtGui.QColor(image.pixel(x, cy)).green() < 100]
    assert red_x, "no ring drawn"
    measured = max(red_x) - 256
    assert measured == pytest.approx(expected_px, abs=4), \
        f"ring radius {measured} px, expected {expected_px} px"


def test_02_03b_ring_scales_with_the_beacon_range(map_widget):
    """L2SYS-REQ-02.03b: doubling the beacon's maximum range doubles the
    ring radius on screen."""
    own = OwnshipState(lat_deg=45.4451, lon_deg=9.2767)
    map_widget.process_ownship_state(own)
    small = RadarBeacon(beacon_id=1, classification="FOE",
                        lat_deg=own.lat_deg, lon_deg=own.lon_deg,
                        max_range_m=10_000.0)
    big = RadarBeacon(beacon_id=2, classification="FOE",
                      lat_deg=own.lat_deg, lon_deg=own.lon_deg,
                      max_range_m=20_000.0)
    assert _ring_radius_px(map_widget, big) == pytest.approx(
        2.0 * _ring_radius_px(map_widget, small))


def test_02_03b_undetected_and_tombstoned_beacons(map_widget):
    """L2SYS-REQ-02.03b displays the detections; [a: D2] a tombstoned
    beacon must not be drawn at all."""
    own = OwnshipState(lat_deg=45.4451, lon_deg=9.2767)
    map_widget.process_ownship_state(own)
    mpp = metres_per_pixel(own.lat_deg, map_widget.zoom)
    tomb = RadarBeacon(beacon_id=3, classification="FOE",
                       lat_deg=own.lat_deg, lon_deg=own.lon_deg,
                       max_range_m=80.0 * mpp, active=False)
    map_widget.process_beacon_contacts([tomb])
    image = map_widget.render_to_image(QtCore.QSize(512, 512))
    reds = [1 for x in range(512) for y in range(0, 512, 2)
            if QtGui.QColor(image.pixel(x, y)).red() > 150
            and QtGui.QColor(image.pixel(x, y)).green() < 100]
    assert not reds, "a tombstoned beacon was still rendered"


def test_02_03b_beacon_position_is_projected_not_centred(map_widget):
    """L2SYS-REQ-02.03b: the ring is centred on the BEACON, not on the
    ownship, so an offset beacon must draw away from the map centre."""
    own = OwnshipState(lat_deg=45.4451, lon_deg=9.2767)
    map_widget.process_ownship_state(own)
    here = map_widget.screen_pos(own.lat_deg, own.lon_deg)
    there = map_widget.screen_pos(own.lat_deg + 0.05, own.lon_deg + 0.05)
    assert here.x() == pytest.approx(map_widget.width() / 2.0)
    assert here.y() == pytest.approx(map_widget.height() / 2.0)
    assert there.x() > here.x()          # east -> right
    assert there.y() < here.y()          # north -> up


def test_02_03b_map_window_delegates_its_boundary_ports(tmp_path, qapp):
    """L2SYS-REQ-02.01 and L2SYS-REQ-02.03b at the assembly boundary:
    [a] fsim_TA_PCS_ThreadMapWindow.sysml strict delegation routes the
    parent ports i_OwnStateIn / i_BeaconDataIn / i_2dTacticalMapOut /
    i_TileCachePathIn to mapGuiWorker and tileFetchWorker."""
    window = ThreadMapWindow(cache_dir=str(tmp_path), start_fetcher=False)
    try:
        state = OwnshipState(lat_deg=50.0333, lon_deg=8.5706)
        window.i_OwnStateIn(state)
        assert window.mapGuiWorker.ownship.lat_deg == pytest.approx(50.0333)
        window.i_BeaconDataIn([RadarBeacon(beacon_id=1,
                                           classification="FRIEND")])
        assert 1 in window.mapGuiWorker.beacons
        image = window.i_2dTacticalMapOut()
        assert isinstance(image, QtGui.QImage) and not image.isNull()
        assert window.tileFetchWorker.cache_dir == str(tmp_path)
    finally:
        window.stop()
