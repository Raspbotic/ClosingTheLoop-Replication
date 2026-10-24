"""ThreadMapWindow -- the Tactical2DMap. [a: fsim_TA_PCS_ThreadMapWindow.sysml]

Two submodules, exactly as the TA splits them:

    MapGuiWidget   "Runs on the Main PCS Thread: handles coordinate math and
                   Qt rendering"
        processOwnshipState      -> set_ownship()
        processBeaconContacts    -> set_beacons()
        calculateTileCoordinates -> flightsim.pcs.slippy (Qt-free)
        render2dTacticalMap      -> paintEvent()
        push2dTacticalMap        -> update() / the repaint signal

    TileFetcher    "Runs on a background QThread: handles asynchronous HTTPS
                   requests"
        requestExternalTiles     -> _fetch()
        cacheMapTiles            -> TileCache

[a: L2SYS-REQ-02.01] fetch and render OSM tiles for the aircraft's current GPS
    coordinates.
[a: L2SYS-REQ-02.03b] display the threat detections as scaled, colour-coded
    rings on the tactical map.
[a: D24] 256 px Web-Mercator tiles, default zoom 12, clamp 2-18, ring colours,
    dashed undetected contacts, disk cache never re-fetched, and placeholders
    with no network.

The coordinate maths lives in `flightsim/pcs/slippy.py` and the cache in
`TileCache` below, both Qt-free, so both are verifiable without a display.
[a: R-7]
"""

from __future__ import annotations

import hashlib
import os
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from flightsim.common import ports
from flightsim.common.messages import RadarBeacon
from flightsim.pcs import slippy

# ---------------------------------------------------------------------------
# Colours [a: D24]
# ---------------------------------------------------------------------------

#: [a: D24] "Threat rings: FRIEND green, FOE red, UNKNOWN amber"
RING_COLOURS = {
    "FRIEND": "#22c55e",     # green
    "FOE": "#ef4444",        # red
    "UNKNOWN": "#f59e0b",    # amber
}

#: The ownship symbol colour, matching the green-on-black instruments of the
#: reference screenshot. [b: D14, reference screenshot]
OWNSHIP_COLOUR = "#00ff66"

#: Placeholder tile colour, drawn when a tile is neither cached nor fetchable.
#: [a: D24] "With no network the map draws placeholders and raises nothing."
PLACEHOLDER_COLOUR = "#1b1b1b"
PLACEHOLDER_GRID_COLOUR = "#2c2c2c"


def ring_colour(classification: str) -> str:
    """[a: D24]"""
    return RING_COLOURS.get(classification, RING_COLOURS["UNKNOWN"])


# ---------------------------------------------------------------------------
# Disk cache -- Qt-free [a: D24] [a: R-7]
# ---------------------------------------------------------------------------
class TileCache:
    """Tiles cached to disk; a cached tile is never re-fetched. [a: D24]

    The cache directory is a **parameter**, so the suite redirects it into
    `tmp_path` and the application never writes outside it. [a: D18]
    """

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.hits = 0
        self.misses = 0
        self.writes = 0

    def path_for(self, z: int, x: int, y: int) -> str:
        return os.path.join(self.directory, str(z), str(x), f"{y}.png")

    def get(self, z: int, x: int, y: int) -> bytes | None:
        path = self.path_for(z, x, y)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.misses += 1
            return None
        if not data:
            self.misses += 1
            return None
        self.hits += 1
        return data

    def put(self, z: int, x: int, y: int, data: bytes) -> None:
        if not data:
            return
        path = self.path_for(z, x, y)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # write-then-rename, so a crash mid-write cannot leave a truncated
            # tile that would then be treated as cached forever [b: D24]
            tmp = path + f".{hashlib.sha1(data[:64]).hexdigest()[:8]}.part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
            self.writes += 1
        except OSError:
            # a cache that cannot be written is not a failure of the map
            pass

    def has(self, z: int, x: int, y: int) -> bool:
        return os.path.isfile(self.path_for(z, x, y))


# ---------------------------------------------------------------------------
# Fetching -- Qt-free [a: R-7]
# ---------------------------------------------------------------------------
def http_tile_fetch(url: str, timeout: float = 5.0) -> bytes | None:
    """Fetch one tile over HTTPS. Returns None on any failure. [a: D24]

    Standard library only, and every failure is swallowed: "With no network the
    map draws placeholders and raises nothing."

    The suite **never** calls this against the live service; it points
    `TileFetcher(fetch=...)` at a loopback stub instead. [a: R-7]
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={
            # OSM's tile usage policy requires an identifying User-Agent.
            # [c] -- what would settle the exact string: the deployment's own
            # contact details; a generic project identifier is used.
            "User-Agent": "flightsim-tactical-map/1.0 (SysML v2 recreation)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            return response.read()
    except Exception:
        return None


@dataclass(frozen=True)
class TileKey:
    z: int
    x: int
    y: int


class TileSource:
    """Cache-then-fetch, Qt-free. [a: D24] [a: R-7]

    "Tiles are cached to disk and a cached tile is never re-fetched."
    """

    def __init__(
        self,
        cache: TileCache,
        fetch: Callable[[str], bytes | None] | None = None,
        origin: str = ports.OSM_TILE_ORIGIN,
    ) -> None:
        self.cache = cache
        self._fetch = fetch if fetch is not None else http_tile_fetch
        self.origin = origin
        self.fetches = 0

    def url_for(self, key: TileKey) -> str:
        return self.origin + ports.OSM_TILE_PATH.format(z=key.z, x=key.x,
                                                        y=key.y)

    def get(self, key: TileKey) -> bytes | None:
        cached = self.cache.get(key.z, key.x, key.y)
        if cached is not None:
            return cached                       # never re-fetched [a: D24]
        self.fetches += 1
        data = self._fetch(self.url_for(key))
        if data:
            self.cache.put(key.z, key.x, key.y, data)
        return data


# ---------------------------------------------------------------------------
# Qt layer
# ---------------------------------------------------------------------------
def _qt():
    """Import Qt lazily so the Qt-free parts above stay importable alone."""
    from PyQt5 import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


class TileFetcher:
    """The background worker. [a: fsim_TA_PCS_ThreadMapWindow.sysml]

    "Runs on a background QThread: handles asynchronous HTTPS requests."

    Realized as a `QThread` when Qt is available; the queue-draining logic is
    a plain method so it can be driven directly, without a thread, by the rows
    that verify caching. [b: R-7, D18]
    """

    def __init__(
        self,
        source: TileSource,
        on_tile: Callable[[TileKey, bytes], None] | None = None,
    ) -> None:
        self.source = source
        self.on_tile = on_tile
        self.queue: "queue.Queue[TileKey]" = queue.Queue()
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: set[TileKey] = set()
        self._lock = threading.Lock()

    # -- i_TileQueueIn [a: fsim_TA_PCS_ThreadMapWindow.sysml] ----------
    def request(self, key: TileKey) -> bool:
        """Queue a tile. Returns False if it is already cached or queued."""
        if self.source.cache.has(key.z, key.x, key.y):
            return False
        with self._lock:
            if key in self._pending:
                return False
            self._pending.add(key)
        self.queue.put(key)
        return True

    # -- requestExternalTiles + cacheMapTiles --------------------------
    def drain_once(self, timeout: float = 0.05) -> int:
        """Serve everything currently queued. Returns how many were served."""
        served = 0
        while True:
            try:
                key = self.queue.get(timeout=timeout if served == 0 else 0.0)
            except queue.Empty:
                return served
            try:
                data = self.source.get(key)
                if data and self.on_tile is not None:
                    # -- i_TileReadyOut
                    self.on_tile(key, data)
                served += 1
            finally:
                with self._lock:
                    self._pending.discard(key)
                self.queue.task_done()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.drain_once(timeout=0.05)

    def start(self) -> threading.Thread:
        self.stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="tile-fetcher",
                                        daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


class MapGuiWidget:
    """The tactical map. [a: fsim_TA_PCS_ThreadMapWindow.sysml]

    Constructed as a QWidget subclass at runtime; the drawing itself is
    expressed as a list of **draw commands** produced by `render_plan()`, which
    is Qt-free. The rows that verify colour, dash style and ring radius assert
    on that plan, so they check what the widget actually paints without
    needing to read pixels back. [b: R-7, D15]
    """

    def __init__(
        self,
        tile_source: TileSource | None = None,
        zoom: int = slippy.DEFAULT_ZOOM,
        width_px: int = 520,
        height_px: int = 520,
    ) -> None:
        self.tile_source = tile_source
        self._zoom = slippy.clamp_zoom(zoom)
        self.width_px = width_px
        self.height_px = height_px
        self.lat_deg = 0.0
        self.lon_deg = 0.0
        self.heading_deg = 0.0
        self.beacons: list[RadarBeacon] = []
        self.repaints = 0
        self.requested_urls: list[str] = []

    # -- zoom ----------------------------------------------------------
    @property
    def zoom(self) -> int:
        return self._zoom

    @zoom.setter
    def zoom(self, value: int) -> None:
        self._zoom = slippy.clamp_zoom(value)      # [a: D24] clamp 2-18

    def zoom_in(self) -> int:
        self.zoom = self._zoom + 1
        return self._zoom

    def zoom_out(self) -> int:
        self.zoom = self._zoom - 1
        return self._zoom

    # -- processOwnshipState -------------------------------------------
    def set_ownship(self, lat_deg: float, lon_deg: float,
                    heading_deg: float = 0.0) -> None:
        """[a: L2SYS-REQ-02.01] "corresponding to the aircraft's current GPS
        coordinates"."""
        self.lat_deg = float(lat_deg)
        self.lon_deg = float(lon_deg)
        self.heading_deg = float(heading_deg)
        self.push()

    # -- processBeaconContacts -----------------------------------------
    def set_beacons(self, beacons: Iterable[RadarBeacon]) -> None:
        """[a: L2SYS-REQ-02.03b] the detections communicated by the PCS."""
        self.beacons = [b for b in beacons if b.active]
        self.push()

    # -- push2dTacticalMap ---------------------------------------------
    def push(self) -> None:
        self.repaints += 1

    # -- calculateTileCoordinates --------------------------------------
    def visible_tiles(self) -> list[tuple[int, int]]:
        return slippy.tiles_covering(self.lon_deg, self.lat_deg, self._zoom,
                                     self.width_px, self.height_px)

    def request_visible_tiles(self, fetcher: "TileFetcher | None") -> int:
        """Ask the fetcher for everything on screen. [a: L2SYS-REQ-02.01]"""
        if fetcher is None:
            return 0
        n = 0
        for tx, ty in self.visible_tiles():
            key = TileKey(self._zoom, tx, ty)
            self.requested_urls.append(fetcher.source.url_for(key))
            if fetcher.request(key):
                n += 1
        return n

    # -- render2dTacticalMap, as a Qt-free plan ------------------------
    def render_plan(self) -> list[dict]:
        """Everything the widget will paint, as data. [b: R-7]

        Tiles first, then rings, then the ownship symbol, so the ownship is on
        top. Ring radii are computed through the Web-Mercator ground resolution
        at the ownship latitude, which is what makes them **scaled**.
        [a: L2SYS-REQ-02.03b]
        """
        plan: list[dict] = []
        cx = self.width_px / 2.0
        cy = self.height_px / 2.0

        # -- tiles
        centre_x, centre_y = slippy.lonlat_to_tile_xy(self.lon_deg,
                                                      self.lat_deg, self._zoom)
        for tx, ty in self.visible_tiles():
            ox = (tx - centre_x) * slippy.TILE_SIZE_PX + cx
            oy = (ty - centre_y) * slippy.TILE_SIZE_PX + cy
            key = TileKey(self._zoom, tx, ty)
            data = None
            if self.tile_source is not None:
                data = self.tile_source.cache.get(key.z, key.x, key.y)
            plan.append({
                "kind": "tile" if data else "placeholder",
                "x": ox, "y": oy,
                "size": slippy.TILE_SIZE_PX,
                "tile": (key.z, key.x, key.y),
                "bytes": len(data) if data else 0,
            })

        # -- threat rings [a: L2SYS-REQ-02.03b] [a: D24]
        for beacon in self.beacons:
            dx, dy = slippy.pixel_offset(beacon.lon, beacon.lat,
                                         self.lon_deg, self.lat_deg,
                                         self._zoom)
            radius_px = slippy.metres_to_pixels(beacon.max_range_m,
                                                self.lat_deg, self._zoom)
            plan.append({
                "kind": "ring",
                "beacon_id": beacon.beacon_id,
                "classification": beacon.classification,
                "x": cx + dx, "y": cy + dy,
                "radius_px": radius_px,
                "colour": ring_colour(beacon.classification),
                # [a: D24] "an undetected contact is drawn dashed in the same
                # colour"
                "style": "solid" if beacon.detected else "dashed",
                "detected": beacon.detected,
            })

        # -- ownship symbol
        plan.append({
            "kind": "ownship",
            "x": cx, "y": cy,
            "heading_deg": self.heading_deg,
            "colour": OWNSHIP_COLOUR,
        })
        # -- the zoom caption of the reference screenshot ("Zoom: 12")
        plan.append({
            "kind": "caption",
            "text": f"Zoom: {self._zoom}",
            "scale_m": slippy.scale_bar_metres(self.lat_deg, self._zoom),
        })
        return plan


# ---------------------------------------------------------------------------
# The Qt widget itself
# ---------------------------------------------------------------------------
def build_map_widget(model: MapGuiWidget, parent=None):
    """Wrap a `MapGuiWidget` model in a real QWidget that paints its plan.

    Kept as a factory rather than a class so that importing this module does
    not require Qt, which is what lets the Qt-free rows run alone. [b: R-7]
    """
    QtCore, QtGui, QtWidgets = _qt()

    class _MapCanvas(QtWidgets.QWidget):
        def __init__(self, model: MapGuiWidget, parent=None):
            super().__init__(parent)
            self.model = model
            self.setMinimumSize(model.width_px, model.height_px)
            self.setWindowTitle("PCS - Tactical GPS Map (Live)")
            self._pixmaps: dict[tuple[int, int, int], object] = {}

        def tile_ready(self, key: TileKey, data: bytes) -> None:
            pix = QtGui.QPixmap()
            if pix.loadFromData(data):
                self._pixmaps[(key.z, key.x, key.y)] = pix
                self.update()

        def resizeEvent(self, event):        # noqa: N802 - Qt naming
            self.model.width_px = self.width()
            self.model.height_px = self.height()
            super().resizeEvent(event)

        def wheelEvent(self, event):         # noqa: N802 - Qt naming
            if event.angleDelta().y() > 0:
                self.model.zoom_in()
            else:
                self.model.zoom_out()
            self.update()

        def paintEvent(self, event):         # noqa: N802 - Qt naming
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.fillRect(self.rect(), QtGui.QColor(PLACEHOLDER_COLOUR))
            for item in self.model.render_plan():
                kind = item["kind"]
                if kind in ("tile", "placeholder"):
                    self._paint_tile(painter, item)
                elif kind == "ring":
                    self._paint_ring(painter, item)
                elif kind == "ownship":
                    self._paint_ownship(painter, item)
                elif kind == "caption":
                    self._paint_caption(painter, item)
            painter.end()

        def _paint_tile(self, painter, item):
            size = item["size"]
            rect = QtCore.QRectF(item["x"], item["y"], size, size)
            pix = self._pixmaps.get(item["tile"])
            if pix is not None:
                painter.drawPixmap(rect.toRect(), pix)
                return
            # [a: D24] placeholders, and nothing raised
            painter.fillRect(rect, QtGui.QColor(PLACEHOLDER_COLOUR))
            pen = QtGui.QPen(QtGui.QColor(PLACEHOLDER_GRID_COLOUR))
            painter.setPen(pen)
            painter.drawRect(rect)

        def _paint_ring(self, painter, item):
            colour = QtGui.QColor(item["colour"])
            pen = QtGui.QPen(colour, 2)
            if item["style"] == "dashed":
                pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            r = item["radius_px"]
            painter.drawEllipse(QtCore.QPointF(item["x"], item["y"]), r, r)
            painter.drawText(QtCore.QPointF(item["x"] + 6, item["y"] - 6),
                             item["beacon_id"])

        def _paint_ownship(self, painter, item):
            painter.save()
            painter.translate(item["x"], item["y"])
            painter.rotate(item["heading_deg"])
            pen = QtGui.QPen(QtGui.QColor(item["colour"]), 2)
            painter.setPen(pen)
            painter.setBrush(QtGui.QColor(item["colour"]))
            shape = QtGui.QPolygonF([
                QtCore.QPointF(0, -10), QtCore.QPointF(7, 8),
                QtCore.QPointF(0, 4), QtCore.QPointF(-7, 8),
            ])
            painter.drawPolygon(shape)
            painter.restore()

        def _paint_caption(self, painter, item):
            painter.setPen(QtGui.QPen(QtGui.QColor(OWNSHIP_COLOUR)))
            painter.drawText(10, self.height() - 26, item["text"])
            painter.drawText(10, self.height() - 10,
                             f"{item['scale_m'] / 1000.0:.2f} km")

    return _MapCanvas(model, parent)


class ThreadMapWindow:
    """The assembly: `mapGuiWorker` + `tileFetchWorker`, with the TA's strict
    delegation. [a: fsim_TA_PCS_ThreadMapWindow.sysml]
    """

    def __init__(
        self,
        cache_directory: str,
        fetch: Callable[[str], bytes | None] | None = None,
        zoom: int = slippy.DEFAULT_ZOOM,
        origin: str = ports.OSM_TILE_ORIGIN,
    ) -> None:
        self.cache = TileCache(cache_directory)             # i_TileCachePathIn
        self.source = TileSource(self.cache, fetch=fetch, origin=origin)
        self.mapGuiWorker = MapGuiWidget(self.source, zoom=zoom)
        self.tileFetchWorker = TileFetcher(self.source)
        self.canvas = None

    def build_widget(self, parent=None):
        self.canvas = build_map_widget(self.mapGuiWorker, parent)
        self.tileFetchWorker.on_tile = self.canvas.tile_ready
        return self.canvas

    # -- i_OwnStateIn --------------------------------------------------
    def set_ownship(self, lat_deg: float, lon_deg: float,
                    heading_deg: float = 0.0) -> None:
        self.mapGuiWorker.set_ownship(lat_deg, lon_deg, heading_deg)
        self.mapGuiWorker.request_visible_tiles(self.tileFetchWorker)

    # -- i_BeaconDataIn ------------------------------------------------
    def set_beacons(self, beacons: Sequence[RadarBeacon]) -> None:
        self.mapGuiWorker.set_beacons(beacons)

    def start(self):
        return self.tileFetchWorker.start()

    def stop(self) -> None:
        self.tileFetchWorker.stop()
