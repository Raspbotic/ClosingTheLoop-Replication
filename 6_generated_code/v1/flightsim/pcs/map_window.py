"""
flightsim/pcs/map_window.py
===========================
Tactical 2D map: slippy-map rendering with asynchronous tile fetching.

TRACEABILITY
------------
[a] Implements part def ThreadMapWindow (fsim_TA_PCS_ThreadMapWindow.sysml),
    whose doc comment names this file: "Map Rendering Layer with
    Asynchronous Tile Fetching (map_window.py)". ThreadMapWindow
    specializes FlightSimSystem::PilotControlStation::Tactical2DMap
    (fsim_LA.sysml).
[a] Submodule 1 MapGuiWidget ("Runs on the Main PCS Thread: handles
    coordinate math and Qt rendering") -> class MapGuiWidget, with the TA
    actions processOwnshipState / processBeaconContacts /
    calculateTileCoordinates / render2dTacticalMap / push2dTacticalMap.
[a] Submodule 2 TileFetcher ("Runs on a background QThread: handles
    asynchronous HTTPS requests") -> class TileFetcher, with the TA actions
    requestExternalTiles / cacheMapTiles, external port e_HttpMapTiles
    (address "tile.openstreetmap.org").
[a] Ports i_OwnStateIn / i_BeaconDataIn / i_2dTacticalMapOut /
    i_TileCachePathIn per the TA, plus the internal i_TileQueueOut /
    i_TileReadyIn pair between the two submodules.

REQUIREMENTS IMPLEMENTED HERE
-----------------------------
[a] L2SYS-REQ-02.01  - fetch and render OpenStreetMap tiles corresponding
                       to the aircraft's current GPS coordinates.
[a] L2SYS-REQ-02.03b - display the threat detections as scaled,
                       color-coded rings on the tactical map.
[a] L2SYS-REQ-05.02  - integrate the OpenStreetMap tile service (V-Method
                       D; the code path is here, the demonstration is
                       manual item M-9).
[a: D4]  tiles from https://tile.openstreetmap.org.
"""

from __future__ import annotations

import math
import os
import tempfile
from typing import Dict, Iterable, Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from flightsim.common import ports
from flightsim.common.messages import OwnshipState, RadarBeacon

TILE_SIZE = 256                       # [b] OSM standard tile edge, pixels
EARTH_RADIUS_M = 6_371_000.0
EARTH_CIRCUMFERENCE_M = 2.0 * math.pi * EARTH_RADIUS_M

#: [a] L2SYS-REQ-02.03b "color-coded rings"; the classification set is
#: fixed by 03.02 / D1. Colours are a developer choice [c]: the reference
#: screenshot shows the tactical picture in the usual NATO-style scheme.
CLASSIFICATION_COLORS: Dict[str, Tuple[int, int, int]] = {
    "FOE": (230, 40, 40),        # red
    "FRIEND": (40, 210, 90),     # green
    "UNKNOWN": (240, 190, 40),   # amber
}


# ==========================================================================
# Slippy-map mathematics (TA action calculateTileCoordinates)
# ==========================================================================

def deg_to_tile_xy(lat_deg: float, lon_deg: float, zoom: int
                   ) -> Tuple[float, float]:
    """Web-Mercator tile coordinates (fractional).

    [a] L2SYS-REQ-02.01: tiles "corresponding to the aircraft's current
        GPS coordinates".
    [b] The standard slippy-map transform of the tile service named in D4:
        x = (lon + 180) / 360 * 2^z
        y = (1 - ln(tan(lat) + sec(lat)) / pi) / 2 * 2^z
    """
    n = 2.0 ** int(zoom)
    lat_rad = math.radians(max(-85.05112878, min(85.05112878, float(lat_deg))))
    x = (float(lon_deg) + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad))
         / math.pi) / 2.0 * n
    return x, y


def tile_xy_to_deg(x: float, y: float, zoom: int) -> Tuple[float, float]:
    """[b] Inverse of deg_to_tile_xy, used to place the ownship symbol."""
    n = 2.0 ** int(zoom)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


def metres_per_pixel(lat_deg: float, zoom: int) -> float:
    """[b] Ground resolution of the Web-Mercator projection; needed to
    scale the threat rings of 02.03b."""
    return (EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat_deg))
            / (TILE_SIZE * 2.0 ** int(zoom)))


def tile_url(zoom: int, x: int, y: int) -> str:
    """[a: D4] OSM tiles at https://tile.openstreetmap.org."""
    return ports.OSM_TILE_URL_TEMPLATE.format(z=int(zoom), x=int(x), y=int(y))


def default_cache_dir() -> str:
    """Port i_TileCachePathIn default. [b] a per-user temp directory."""
    path = os.path.join(tempfile.gettempdir(), "fsim_tile_cache")
    os.makedirs(path, exist_ok=True)
    return path


# ==========================================================================
# Submodule 2: TileFetcher (background QThread)
# ==========================================================================

class TileFetcher(QtCore.QThread):
    """part def TileFetcher (fsim_TA_PCS_ThreadMapWindow.sysml).

    "Runs on a background QThread: handles asynchronous HTTPS requests".
    TA actions requestExternalTiles and cacheMapTiles.
    [a: D15] carries a stop flag so no test leaks a live network thread.
    """

    #: port i_TileReadyOut -> MapGuiWidget.i_TileReadyIn
    tile_ready = QtCore.pyqtSignal(int, int, int, bytes)

    def __init__(self, cache_dir: Optional[str] = None,
                 base_url: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("tile-fetcher")
        # port i_TileCachePathIn
        self.cache_dir = cache_dir or default_cache_dir()
        os.makedirs(self.cache_dir, exist_ok=True)
        self.base_url = base_url            # None -> the D4 OSM endpoint
        self._queue: list = []              # port i_TileQueueIn
        self._mutex = QtCore.QMutex()
        self._stop = False
        self.fetched_from_network = 0
        self.served_from_cache = 0

    # -- cacheMapTiles ---------------------------------------------------
    def cache_path(self, zoom: int, x: int, y: int) -> str:
        return os.path.join(self.cache_dir, f"{zoom}_{x}_{y}.png")

    def read_cache(self, zoom: int, x: int, y: int) -> Optional[bytes]:
        """TA action cacheMapTiles (read side)."""
        path = self.cache_path(zoom, x, y)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    return fh.read()
            except OSError:
                return None
        return None

    def write_cache(self, zoom: int, x: int, y: int, data: bytes) -> None:
        """TA action cacheMapTiles (write side): disk tile cache."""
        try:
            with open(self.cache_path(zoom, x, y), "wb") as fh:
                fh.write(data)
        except OSError:
            pass

    # -- requestExternalTiles --------------------------------------------
    def request(self, zoom: int, x: int, y: int) -> None:
        """Port i_TileQueueIn: enqueue one tile request."""
        with QtCore.QMutexLocker(self._mutex):
            key = (int(zoom), int(x), int(y))
            if key not in self._queue:
                self._queue.append(key)

    def fetch_one(self, zoom: int, x: int, y: int) -> Optional[bytes]:
        """Cache-first tile retrieval.

        [a] L2SYS-REQ-02.01 "fetch ... OpenStreetMap tiles".
        [a: D4] endpoint https://tile.openstreetmap.org.
        """
        cached = self.read_cache(zoom, x, y)
        if cached is not None:
            self.served_from_cache += 1
            return cached
        url = (tile_url(zoom, x, y) if self.base_url is None
               else f"{self.base_url}/{zoom}/{x}/{y}.png")
        try:
            import requests
            resp = requests.get(
                url, timeout=5.0,
                headers={"User-Agent": ports.OSM_USER_AGENT})
            if resp.status_code != 200 or not resp.content:
                return None
            data = resp.content
        except Exception:                     # [b] the map degrades to the
            return None                       # grid when offline
        self.fetched_from_network += 1
        self.write_cache(zoom, x, y, data)
        return data

    def run(self) -> None:
        while not self._stop:
            with QtCore.QMutexLocker(self._mutex):
                item = self._queue.pop(0) if self._queue else None
            if item is None:
                self.msleep(30)
                continue
            zoom, x, y = item
            data = self.fetch_one(zoom, x, y)
            if data:
                self.tile_ready.emit(zoom, x, y, data)   # i_TileReadyOut

    def stop(self) -> None:
        """[a: D15] stoppable worker."""
        self._stop = True
        self.wait(3000)


# ==========================================================================
# Submodule 1: MapGuiWidget (Qt main thread)
# ==========================================================================

class MapGuiWidget(QtWidgets.QWidget):
    """part def MapGuiWidget (fsim_TA_PCS_ThreadMapWindow.sysml).

    "Runs on the Main PCS Thread: handles coordinate math and Qt
    rendering."
    """

    def __init__(self, fetcher: Optional[TileFetcher] = None, zoom: int = 12,
                 parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 360)
        self.zoom = int(zoom)
        self.ownship = OwnshipState(lat_deg=45.4451, lon_deg=9.2767)
        self.beacons: Dict[int, RadarBeacon] = {}
        self._tiles: Dict[Tuple[int, int, int], QtGui.QPixmap] = {}
        self.fetcher = fetcher
        if self.fetcher is not None:
            self.fetcher.tile_ready.connect(self.on_tile_ready)
        self.requested: list = []

    # -- ports -----------------------------------------------------------
    def process_ownship_state(self, state: OwnshipState) -> None:
        """TA action processOwnshipState; port i_OwnStateIn.
        [a] L2SYS-REQ-02.01: the map follows the aircraft's GPS position."""
        self.ownship = state
        self.calculate_tile_coordinates()
        self.update()

    def process_beacon_contacts(self, beacons: Iterable[RadarBeacon]) -> None:
        """TA action processBeaconContacts; port i_BeaconDataIn.
        [a] L2SYS-REQ-02.03b: the detections to be drawn as rings."""
        self.beacons = {b.beacon_id: b for b in beacons}
        self.update()

    # -- calculateTileCoordinates ----------------------------------------
    def center_tile(self) -> Tuple[float, float]:
        """Fractional tile coordinates of the ownship. [a] 02.01."""
        return deg_to_tile_xy(self.ownship.lat_deg, self.ownship.lon_deg,
                              self.zoom)

    def visible_tiles(self):
        """TA action calculateTileCoordinates: the integer tile indices
        covering the widget around the ownship."""
        cx, cy = self.center_tile()
        half_w = self.width() / (2.0 * TILE_SIZE)
        half_h = self.height() / (2.0 * TILE_SIZE)
        n = int(2 ** self.zoom)
        out = []
        for tx in range(math.floor(cx - half_w), math.ceil(cx + half_w) + 1):
            for ty in range(math.floor(cy - half_h),
                            math.ceil(cy + half_h) + 1):
                if 0 <= ty < n:
                    out.append((tx % n, ty))
        return out

    def calculate_tile_coordinates(self) -> None:
        """Queue any visible tile that is not yet held. Port
        i_TileQueueOut -> TileFetcher.i_TileQueueIn."""
        for tx, ty in self.visible_tiles():
            key = (self.zoom, tx, ty)
            if key in self._tiles:
                continue
            if key not in self.requested:
                self.requested.append(key)
                if self.fetcher is not None:
                    self.fetcher.request(self.zoom, tx, ty)

    def on_tile_ready(self, zoom: int, x: int, y: int, data: bytes) -> None:
        """Port i_TileReadyIn."""
        pix = QtGui.QPixmap()
        if pix.loadFromData(data):
            self._tiles[(int(zoom), int(x), int(y))] = pix
            self.update()

    # -- rendering -------------------------------------------------------
    def metres_per_pixel(self) -> float:
        return metres_per_pixel(self.ownship.lat_deg, self.zoom)

    def screen_pos(self, lat_deg: float, lon_deg: float) -> QtCore.QPointF:
        """[b] Project a geographic point into widget pixels."""
        cx, cy = self.center_tile()
        x, y = deg_to_tile_xy(lat_deg, lon_deg, self.zoom)
        return QtCore.QPointF(self.width() / 2.0 + (x - cx) * TILE_SIZE,
                              self.height() / 2.0 + (y - cy) * TILE_SIZE)

    def render_to_image(self, size: Optional[QtCore.QSize] = None
                        ) -> QtGui.QImage:
        """TA action render2dTacticalMap / push2dTacticalMap (port
        i_2dTacticalMapOut). Offscreen-renderable so 02.03b is testable."""
        size = size or self.size()
        image = QtGui.QImage(size, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor(24, 28, 24))
        painter = QtGui.QPainter(image)
        self._paint(painter, size.width(), size.height())
        painter.end()
        return image

    def paintEvent(self, event):                    # noqa: N802 (Qt name)
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(24, 28, 24))
        self._paint(painter, self.width(), self.height())
        painter.end()

    def _paint(self, painter: QtGui.QPainter, w: int, h: int) -> None:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        cx, cy = self.center_tile()

        # -- tiles (02.01) ----------------------------------------------
        for (z, tx, ty), pix in self._tiles.items():
            if z != self.zoom:
                continue
            px = w / 2.0 + (tx - cx) * TILE_SIZE
            py = h / 2.0 + (ty - cy) * TILE_SIZE
            painter.drawPixmap(int(px), int(py), pix)

        # -- threat rings (02.03b) --------------------------------------
        mpp = self.metres_per_pixel()
        for beacon in self.beacons.values():
            if not beacon.active:                    # [a: D2] tombstoned
                continue
            colour = QtGui.QColor(*CLASSIFICATION_COLORS.get(
                beacon.classification, CLASSIFICATION_COLORS["UNKNOWN"]))
            centre = self.screen_pos(beacon.lat_deg, beacon.lon_deg)
            # [a] 02.03b "scaled": the ring radius is the beacon's maximum
            # detection range converted through the map scale.
            radius_px = beacon.max_range_m / mpp if mpp > 0 else 0.0
            pen = QtGui.QPen(colour)
            pen.setWidth(3 if beacon.detected else 2)
            if not beacon.detected:
                pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawEllipse(centre, radius_px, radius_px)
            painter.setBrush(QtGui.QBrush(colour))
            painter.drawEllipse(centre, 4.0, 4.0)
            painter.setBrush(QtCore.Qt.NoBrush)

        # -- ownship symbol ---------------------------------------------
        painter.setPen(QtGui.QPen(QtGui.QColor(60, 255, 60), 2))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(60, 255, 60)))
        painter.drawEllipse(QtCore.QPointF(w / 2.0, h / 2.0), 6.0, 6.0)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawText(8, h - 24, f"Zoom: {self.zoom}")
        painter.drawText(8, h - 8, f"{mpp * 100.0:,.0f} m / 100 px")


# ==========================================================================
# Assembly: ThreadMapWindow
# ==========================================================================

class ThreadMapWindow(QtWidgets.QMainWindow):
    """part def ThreadMapWindow (fsim_TA_PCS_ThreadMapWindow.sysml).

    Strict delegation: the parent boundary ports are routed to
    mapGuiWorker and tileFetchWorker exactly as the TA binds them.
    """

    def __init__(self, cache_dir: Optional[str] = None,
                 base_url: Optional[str] = None, zoom: int = 12,
                 start_fetcher: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PCS - Tactical GPS Map (Live)")
        # part tileFetchWorker
        self.tileFetchWorker = TileFetcher(cache_dir=cache_dir,
                                           base_url=base_url)
        # part mapGuiWorker
        self.mapGuiWorker = MapGuiWidget(self.tileFetchWorker, zoom=zoom)
        self.setCentralWidget(self.mapGuiWorker)
        self.resize(520, 520)
        if start_fetcher:
            self.tileFetchWorker.start()

    # -- parent boundary ports (strict delegation) ----------------------
    def i_OwnStateIn(self, state: OwnshipState) -> None:   # noqa: N802
        self.mapGuiWorker.process_ownship_state(state)

    def i_BeaconDataIn(self, beacons) -> None:             # noqa: N802
        self.mapGuiWorker.process_beacon_contacts(beacons)

    def i_2dTacticalMapOut(self) -> QtGui.QImage:          # noqa: N802
        return self.mapGuiWorker.render_to_image()

    def closeEvent(self, event):                           # noqa: N802
        self.tileFetchWorker.stop()
        super().closeEvent(event)

    def stop(self) -> None:
        """[a: D15]"""
        self.tileFetchWorker.stop()


__all__ = ["MapGuiWidget", "TileFetcher", "ThreadMapWindow",
           "deg_to_tile_xy", "tile_xy_to_deg", "metres_per_pixel",
           "tile_url", "CLASSIFICATION_COLORS", "TILE_SIZE",
           "default_cache_dir"]
