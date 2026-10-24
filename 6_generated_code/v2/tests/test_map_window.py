"""Tactical2DMap.

Plan rows: P-02.01-1 .. -6, P-02.03b-1 .. -5.

[a: R-7 acceptance] "the applicable plan rows are green with no external network
access." Every fetch in this module goes to a **loopback stub** bound to an
ephemeral port, never to tile.openstreetmap.org. The one row that names the real
origin asserts on the URL string only and makes no request.

Every cache directory is inside `tmp_path`. [a: D18]
"""

from __future__ import annotations

import http.server
import math
import threading

import pytest

from flightsim.common import ports
from flightsim.common.messages import RadarBeacon
from flightsim.pcs import map_window, slippy
from flightsim.pcs.map_window import (
    MapGuiWidget,
    ThreadMapWindow,
    TileCache,
    TileFetcher,
    TileKey,
    TileSource,
)

# [a: D24], transcribed.
D24_TILE_PX = 256
D24_DEFAULT_ZOOM = 12
D24_MIN_ZOOM = 2
D24_MAX_ZOOM = 18
D24_COLOURS = {"FRIEND": "green", "FOE": "red", "UNKNOWN": "amber"}

#: The smallest valid PNG: a 1x1 transparent image. Serving real bytes means
#: the cache rows exercise the real write/read path.
ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100055c0b5a000000"
    "0049454e44ae426082"
)


class TileStub:
    """A loopback HTTP server that serves one PNG and counts requests.

    Bound to 127.0.0.1 on an ephemeral port, so nothing leaves the machine.
    [a: R-7]
    """

    def __init__(self, body: bytes = ONE_PIXEL_PNG, status: int = 200):
        self.requests: list[str] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                       # noqa: N802 - http.server
                outer.requests.append(self.path)
                if status != 200:
                    self.send_error(status)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):              # silence the test output
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="tile-stub", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


@pytest.fixture
def tile_stub():
    stub = TileStub()
    yield stub
    stub.close()


@pytest.fixture
def cache(tmp_path):
    return TileCache(str(tmp_path / "tiles"))


def beacon(beacon_id="B1", classification="FOE", lat=45.5, lon=9.3,
           max_range_m=10_000.0, detected=True, active=True):
    return RadarBeacon(beacon_id=beacon_id, classification=classification,
                       lat=lat, lon=lon, alt=0.0, max_range_m=max_range_m,
                       detected=detected, slant_range_m=1000.0, active=active)


def rings(plan):
    return [i for i in plan if i["kind"] == "ring"]


# ===========================================================================
# P-02.01-1 -- the slippy arithmetic, hand computed
# ===========================================================================
def test_slippy_arithmetic_hand_computed():
    """P-02.01-1. [a: D24] Web-Mercator tile coordinates.

    Hand computation at (lat 0, lon 0, z 12):
        n = 2 ** 12 = 4096
        x = (0 + 180) / 360 * 4096 = 0.5 * 4096 = 2048
        y = (1 - ln(tan(0) + sec(0)) / pi) / 2 * 4096
          = (1 - ln(0 + 1) / pi) / 2 * 4096
          = (1 - 0) / 2 * 4096 = 2048

    And at (lat 45.4451, lon 9.2767, z 12), longhand:
        x = (9.2767 + 180) / 360 * 4096
        y = (1 - ln(tan(phi) + 1/cos(phi)) / pi) / 2 * 4096,  phi in radians
    """
    x, y = slippy.lonlat_to_tile_xy(0.0, 0.0, 12)
    assert x == pytest.approx(2048.0)
    assert y == pytest.approx(2048.0)

    n = 2.0 ** 12
    lon, lat = 9.2767, 45.4451
    phi = math.radians(lat)
    expected_x = (lon + 180.0) / 360.0 * n
    expected_y = (1.0 - math.log(math.tan(phi) + 1.0 / math.cos(phi))
                  / math.pi) / 2.0 * n
    gx, gy = slippy.lonlat_to_tile_xy(lon, lat, 12)
    assert gx == pytest.approx(expected_x, rel=1e-12)
    assert gy == pytest.approx(expected_y, rel=1e-12)
    assert slippy.lonlat_to_tile(lon, lat, 12) == (int(expected_x),
                                                   int(expected_y))


def test_tile_coordinates_round_trip():
    """P-02.01-1. The inverse agrees with the forward transform, which is what
    makes the pixel offsets meaningful. [b: D24]"""
    for lon, lat, z in ((9.2767, 45.4451, 12), (-122.375, 37.6188, 14),
                        (8.5706, 50.0333, 6)):
        x, y = slippy.lonlat_to_tile_xy(lon, lat, z)
        back_lon, back_lat = slippy.tile_to_lonlat(x, y, z)
        assert back_lon == pytest.approx(lon, abs=1e-9)
        assert back_lat == pytest.approx(lat, abs=1e-9)


# ===========================================================================
# P-02.01-2 -- zoom clamp
# ===========================================================================
def test_zoom_is_clamped_two_to_eighteen():
    """P-02.01-2. [a: D24] "default zoom 12; zoom clamped 2-18"."""
    assert slippy.DEFAULT_ZOOM == D24_DEFAULT_ZOOM == 12
    assert slippy.MIN_ZOOM == D24_MIN_ZOOM == 2
    assert slippy.MAX_ZOOM == D24_MAX_ZOOM == 18
    assert [slippy.clamp_zoom(z) for z in (0, 1, 2, 18, 19, 40, -5)] == (
        [2, 2, 2, 18, 18, 18, 2])


def test_widget_zoom_is_clamped(cache):
    """P-02.01-2. The clamp holds on the widget, not only in the helper."""
    m = MapGuiWidget(TileSource(cache))
    assert m.zoom == 12
    for _ in range(20):
        m.zoom_in()
    assert m.zoom == 18
    for _ in range(40):
        m.zoom_out()
    assert m.zoom == 2


# ===========================================================================
# P-02.01-3 -- the URL follows the ownship position
# ===========================================================================
def test_tile_url_follows_the_ownship_position(cache, tile_stub):
    """P-02.01-3. [a: L2SYS-REQ-02.01] "tiles corresponding to the aircraft's
    current GPS coordinates". Two positions in different tiles produce
    different tile URLs, each naming the tile that position falls in.
    """
    source = TileSource(cache, fetch=lambda url: None, origin=tile_stub.origin)
    m = MapGuiWidget(source, width_px=256, height_px=256)
    fetcher = TileFetcher(source)

    m.set_ownship(45.4451, 9.2767)
    m.requested_urls.clear()
    m.request_visible_tiles(fetcher)
    first = set(m.requested_urls)

    m.set_ownship(37.6188, -122.3750)
    m.requested_urls.clear()
    m.request_visible_tiles(fetcher)
    second = set(m.requested_urls)

    assert first and second
    assert first != second
    z, x, y = 12, *slippy.lonlat_to_tile(9.2767, 45.4451, 12)
    assert any(f"/12/{x}/{y}.png" in u for u in first)
    z2, x2, y2 = 12, *slippy.lonlat_to_tile(-122.3750, 37.6188, 12)
    assert any(f"/12/{x2}/{y2}.png" in u for u in second)


def test_tile_url_uses_the_d4_origin(cache):
    """P-02.01-3. [a: D4] "OSM tiles at https://tile.openstreetmap.org".
    Asserted on the string; **no request is made**.
    """
    source = TileSource(cache)                        # default origin
    url = source.url_for(TileKey(12, 2153, 1466))
    assert url == "https://tile.openstreetmap.org/12/2153/1466.png"
    assert url.startswith(ports.OSM_TILE_ORIGIN)
    assert ports.OSM_TILE_HOST == "tile.openstreetmap.org"


# ===========================================================================
# P-02.01-4 -- fetched from a loopback stub and cached
# ===========================================================================
def test_tile_is_fetched_from_a_loopback_stub_and_cached(cache, tile_stub):
    """P-02.01-4. [a: D24] "Tiles are cached to disk and a cached tile is never
    re-fetched." [a: R-7] "Network fetches are verified against a loopback
    stub, never the live service."
    """
    source = TileSource(cache, origin=tile_stub.origin)
    key = TileKey(12, 2153, 1466)

    first = source.get(key)
    assert first == ONE_PIXEL_PNG
    assert len(tile_stub.requests) == 1
    assert tile_stub.requests[0] == "/12/2153/1466.png"
    assert cache.has(12, 2153, 1466)

    second = source.get(key)
    assert second == ONE_PIXEL_PNG
    assert len(tile_stub.requests) == 1, (
        "a cached tile was re-fetched"
    )
    assert source.fetches == 1


def test_cached_tile_is_not_refetched_after_a_restart(tmp_path, tile_stub):
    """P-02.01-4. The cache is on **disk**, so it survives the process that
    wrote it -- which is what "cached to disk" means. [a: D24]
    """
    directory = str(tmp_path / "tiles")
    key = TileKey(9, 271, 185)

    first = TileSource(TileCache(directory), origin=tile_stub.origin)
    assert first.get(key) == ONE_PIXEL_PNG
    assert len(tile_stub.requests) == 1

    # a brand-new cache and source over the same directory
    second = TileSource(TileCache(directory), origin=tile_stub.origin)
    assert second.get(key) == ONE_PIXEL_PNG
    assert len(tile_stub.requests) == 1
    assert second.fetches == 0


def test_fetcher_serves_its_queue_and_reports_ready_tiles(cache, tile_stub):
    """P-02.01-4. The TileFetcher submodule: queue in, tile out.
    [a: fsim_TA_PCS_ThreadMapWindow.sysml] i_TileQueueIn / i_TileReadyOut.
    """
    ready: list[tuple[TileKey, int]] = []
    source = TileSource(cache, origin=tile_stub.origin)
    fetcher = TileFetcher(source,
                          on_tile=lambda k, d: ready.append((k, len(d))))

    assert fetcher.request(TileKey(12, 1, 1)) is True
    assert fetcher.request(TileKey(12, 1, 1)) is False    # already queued
    fetcher.drain_once()

    assert len(ready) == 1
    assert ready[0][0] == TileKey(12, 1, 1)
    assert ready[0][1] == len(ONE_PIXEL_PNG)
    # and now that it is cached, requesting it again does not queue it
    assert fetcher.request(TileKey(12, 1, 1)) is False


def test_fetcher_thread_starts_and_stops(cache, tile_stub):
    """P-02.01-4. [a: D15] "Every spawned thread is stoppable (stop_event).\""""
    source = TileSource(cache, origin=tile_stub.origin)
    fetcher = TileFetcher(source)
    thread = fetcher.start()
    assert thread.is_alive()
    fetcher.request(TileKey(11, 5, 5))
    import time
    for _ in range(100):
        if cache.has(11, 5, 5):
            break
        time.sleep(0.02)
    assert cache.has(11, 5, 5)
    fetcher.stop()
    assert not thread.is_alive()


# ===========================================================================
# P-02.01-5 -- no network
# ===========================================================================
def test_no_network_draws_placeholders_and_raises_nothing(cache):
    """P-02.01-5. [a: D24] "With no network the map draws placeholders and
    raises nothing."

    The fetch function is pointed at a closed port, so it fails the way a real
    outage does, and nothing propagates.
    """
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()                                   # nothing is listening now

    source = TileSource(cache, origin=f"http://127.0.0.1:{dead_port}")
    m = MapGuiWidget(source, width_px=256, height_px=256)
    m.set_ownship(45.4451, 9.2767)

    assert source.get(TileKey(12, 2153, 1466)) is None    # raises nothing
    plan = m.render_plan()                                # raises nothing
    tiles = [i for i in plan if i["kind"] in ("tile", "placeholder")]
    assert tiles
    assert all(i["kind"] == "placeholder" for i in tiles)


def test_a_failing_fetch_never_raises(cache):
    """P-02.01-5. Every failure mode of the fetch is swallowed. [a: D24]"""
    def explode(url):
        raise ConnectionError("network is down")

    source = TileSource(cache, fetch=lambda url: None)
    assert source.get(TileKey(12, 1, 1)) is None

    # even a fetch that raises must not escape the map
    source2 = TileSource(cache, fetch=map_window.http_tile_fetch,
                         origin="http://127.0.0.1:1")     # nothing listens
    assert source2.get(TileKey(12, 2, 2)) is None
    _ = explode


def test_a_non_200_response_is_not_cached(cache):
    """P-02.01-5. An error page must not be written to the cache and then
    served forever as if it were a tile. [b: D24]"""
    stub = TileStub(status=404)
    try:
        source = TileSource(cache, origin=stub.origin)
        assert source.get(TileKey(12, 3, 3)) is None
        assert not cache.has(12, 3, 3)
    finally:
        stub.close()


# ===========================================================================
# P-02.01-6 -- tile size
# ===========================================================================
def test_tile_size_is_256(cache):
    """P-02.01-6. [a: D24] "256-pixel Web-Mercator tiles"."""
    assert slippy.TILE_SIZE_PX == D24_TILE_PX == 256
    m = MapGuiWidget(TileSource(cache), width_px=512, height_px=512)
    m.set_ownship(45.4451, 9.2767)
    tiles = [i for i in m.render_plan() if i["kind"] in ("tile", "placeholder")]
    assert tiles
    assert all(i["size"] == 256 for i in tiles)


# ===========================================================================
# P-02.03b-1 / -2 -- scaled rings
# ===========================================================================
def test_ring_radius_scales_with_max_range(cache):
    """P-02.03b-1. [a: L2SYS-REQ-02.03b] "scaled ... rings".

    Two beacons at the same latitude with max_range_m 10 000 and 20 000 give
    radii in the ratio 2.0. The scaling is computed longhand here from the
    Web-Mercator ground resolution:

        res = C * cos(lat) / (256 * 2 ** z)
        radius_px = max_range_m / res
    """
    lat, lon, z = 45.4451, 9.2767, 12
    C = 2.0 * math.pi * 6378137.0
    res = C * math.cos(math.radians(lat)) / (256 * (2 ** z))
    assert res == pytest.approx(26.81381787157473, rel=1e-9)

    m = MapGuiWidget(TileSource(cache), zoom=z, width_px=512, height_px=512)
    m.set_ownship(lat, lon)
    m.set_beacons([
        beacon("SMALL", "FOE", lat=lat, lon=lon, max_range_m=10_000.0),
        beacon("BIG", "FOE", lat=lat, lon=lon, max_range_m=20_000.0),
    ])
    by_id = {r["beacon_id"]: r for r in rings(m.render_plan())}

    assert by_id["SMALL"]["radius_px"] == pytest.approx(10_000.0 / res,
                                                        rel=1e-9)
    assert by_id["BIG"]["radius_px"] == pytest.approx(20_000.0 / res, rel=1e-9)
    ratio = by_id["BIG"]["radius_px"] / by_id["SMALL"]["radius_px"]
    assert ratio == pytest.approx(2.0, rel=0.01)


def test_ring_radius_changes_with_zoom(cache):
    """P-02.03b-2. One zoom level halves the ground resolution, so the same
    range draws twice as large. [b: D24]
    """
    lat, lon = 45.4451, 9.2767
    radii = []
    for z in (12, 13):
        m = MapGuiWidget(TileSource(cache), zoom=z, width_px=512,
                         height_px=512)
        m.set_ownship(lat, lon)
        m.set_beacons([beacon("B", "FOE", lat=lat, lon=lon,
                              max_range_m=10_000.0)])
        radii.append(rings(m.render_plan())[0]["radius_px"])
    assert radii[1] / radii[0] == pytest.approx(2.0, rel=0.01)


def test_ring_is_centred_on_the_beacon_not_the_ownship(cache):
    """P-02.03b-1. A ring drawn at the ownship's pixel regardless of where the
    beacon is would pass a radius-only check, so the centre is asserted too.
    [b: L2SYS-REQ-02.03b]
    """
    m = MapGuiWidget(TileSource(cache), zoom=12, width_px=512, height_px=512)
    m.set_ownship(45.4451, 9.2767)
    m.set_beacons([beacon("NE", "FOE", lat=45.6, lon=9.5)])
    ring = rings(m.render_plan())[0]

    dx, dy = slippy.pixel_offset(9.5, 45.6, 9.2767, 45.4451, 12)
    assert ring["x"] == pytest.approx(256.0 + dx)
    assert ring["y"] == pytest.approx(256.0 + dy)
    assert dx > 0, "east of the ownship should be to the right"
    assert dy < 0, "north of the ownship should be up the screen"


# ===========================================================================
# P-02.03b-3 / -4 -- colour and dash
# ===========================================================================
def test_ring_colour_follows_classification(cache):
    """P-02.03b-3. [a: D24] "FRIEND green, FOE red, UNKNOWN amber"."""
    m = MapGuiWidget(TileSource(cache), width_px=512, height_px=512)
    m.set_ownship(45.4451, 9.2767)
    m.set_beacons([
        beacon("F", "FRIEND", lat=45.45, lon=9.28),
        beacon("H", "FOE", lat=45.46, lon=9.29),
        beacon("U", "UNKNOWN", lat=45.47, lon=9.30),
    ])
    by_id = {r["beacon_id"]: r for r in rings(m.render_plan())}

    # The colours are asserted against the RGB triples the names denote, not
    # against the implementation's own constants.
    def rgb(hex_colour):
        h = hex_colour.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    friend = rgb(by_id["F"]["colour"])
    foe = rgb(by_id["H"]["colour"])
    unknown = rgb(by_id["U"]["colour"])

    assert friend[1] > friend[0] and friend[1] > friend[2], "FRIEND is green"
    assert foe[0] > foe[1] and foe[0] > foe[2], "FOE is red"
    # amber: red-dominant with substantial green and little blue
    assert unknown[0] > 200 and 100 < unknown[1] < 220 and unknown[2] < 100, (
        "UNKNOWN is amber"
    )
    assert len({by_id[k]["colour"] for k in "FHU"}) == 3


def test_undetected_contact_is_dashed_same_colour(cache):
    """P-02.03b-4. [a: D24] "an undetected contact is drawn dashed in the same
    colour"."""
    m = MapGuiWidget(TileSource(cache), width_px=512, height_px=512)
    m.set_ownship(45.4451, 9.2767)
    m.set_beacons([
        beacon("SEEN", "FOE", lat=45.45, lon=9.28, detected=True),
        beacon("UNSEEN", "FOE", lat=45.46, lon=9.29, detected=False),
    ])
    by_id = {r["beacon_id"]: r for r in rings(m.render_plan())}

    assert by_id["SEEN"]["style"] == "solid"
    assert by_id["UNSEEN"]["style"] == "dashed"
    assert by_id["SEEN"]["colour"] == by_id["UNSEEN"]["colour"], (
        "the dashed contact must be the SAME colour"
    )


def test_a_tombstoned_beacon_is_not_drawn(cache):
    """P-02.03b-4 x C-D2-4. [a: D2] a tombstone removes the contact; the map
    must stop drawing it."""
    m = MapGuiWidget(TileSource(cache), width_px=512, height_px=512)
    m.set_ownship(45.4451, 9.2767)
    m.set_beacons([beacon("B", "FOE", active=True)])
    assert len(rings(m.render_plan())) == 1
    m.set_beacons([beacon("B", "FOE", active=False)])
    assert rings(m.render_plan()) == []


# ===========================================================================
# P-02.03b-5 -- the ownship symbol
# ===========================================================================
def test_ownship_symbol_is_drawn(cache):
    """P-02.03b-5. The ownship is drawn at the centre of the view, rotated to
    its heading, and on top of the rings. [b: L2SYS-REQ-02.01]
    """
    m = MapGuiWidget(TileSource(cache), width_px=512, height_px=512)
    m.set_ownship(45.4451, 9.2767, heading_deg=176.0)
    m.set_beacons([beacon("B", "FOE")])
    plan = m.render_plan()

    own = [i for i in plan if i["kind"] == "ownship"]
    assert len(own) == 1
    assert own[0]["x"] == pytest.approx(256.0)
    assert own[0]["y"] == pytest.approx(256.0)
    assert own[0]["heading_deg"] == pytest.approx(176.0)
    # drawn after every ring, so it is on top
    assert plan.index(own[0]) > max(plan.index(r) for r in rings(plan))


# ===========================================================================
# The assembly and the real widget
# ===========================================================================
def test_thread_map_window_wires_the_two_submodules(tmp_path, tile_stub):
    """[a: fsim_TA_PCS_ThreadMapWindow.sysml] `mapGuiWorker` + `tileFetchWorker`
    with the TA's delegation.
    """
    tmw = ThreadMapWindow(str(tmp_path / "tiles"), origin=tile_stub.origin)
    assert isinstance(tmw.mapGuiWorker, MapGuiWidget)
    assert isinstance(tmw.tileFetchWorker, TileFetcher)
    assert tmw.tileFetchWorker.source is tmw.mapGuiWorker.tile_source

    tmw.set_ownship(45.4451, 9.2767, 176.0)
    assert tmw.tileFetchWorker.queue.qsize() > 0
    tmw.tileFetchWorker.drain_once()
    assert tile_stub.requests


def test_map_widget_paints_offscreen(qt_widgets, tmp_path, tile_stub):
    """[a: D15] offscreen Qt. The real widget paints its plan without raising,
    with tiles and with placeholders.
    """
    from PyQt5 import QtGui

    tmw = ThreadMapWindow(str(tmp_path / "tiles"), origin=tile_stub.origin)
    canvas = qt_widgets(tmw.build_widget())
    canvas.resize(320, 320)
    tmw.set_ownship(45.4451, 9.2767, 176.0)
    tmw.set_beacons([beacon("B1", "FOE"), beacon("B2", "FRIEND",
                                                 detected=False)])

    image = QtGui.QImage(320, 320, QtGui.QImage.Format_ARGB32)
    canvas.render(image)                        # placeholders, raises nothing

    tmw.tileFetchWorker.on_tile = canvas.tile_ready
    tmw.tileFetchWorker.drain_once()
    canvas.render(image)                        # now with real tiles
    assert tmw.cache.writes > 0
    assert canvas.windowTitle() == "PCS - Tactical GPS Map (Live)"
    tmw.stop()


def test_nothing_is_written_outside_the_cache_directory(tmp_path, tile_stub):
    """[a: D18] "Every path the application writes to by default is a parameter
    the suite can redirect." The cache directory is that parameter.
    """
    cache_dir = tmp_path / "tiles"
    tmw = ThreadMapWindow(str(cache_dir), origin=tile_stub.origin)
    tmw.set_ownship(45.4451, 9.2767)
    tmw.tileFetchWorker.drain_once()
    tmw.stop()

    written = list(cache_dir.rglob("*"))
    assert written, "nothing was cached at all"
    for path in written:
        assert str(path).startswith(str(cache_dir))
