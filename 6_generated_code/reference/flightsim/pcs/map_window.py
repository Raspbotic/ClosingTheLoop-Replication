"""
flightsim.pcs.map_window
========================
ThreadMapWindow: 2D tactical map -- separate top-level window spawned by
the PCS main thread (customer decision 2026-07-11); its TileFetcher runs
on a background QThread.

TRACEABILITY
------------
[a] Implements part def ThreadMapWindow (fsim_TA_PCS_ThreadMapWindow.sysml)
    :> Tactical2DMap (fsim_LA.sysml). TA doc names this file:
    "map_window.py".
[a] Submodules per TA:
      mapGuiWorker    : MapGuiWidget  -- "Runs on the Main PCS Thread:
                        handles coordinate math and Qt rendering"
      tileFetchWorker : TileFetcher   -- "Runs on a background QThread:
                        handles asynchronous HTTPS requests"
[a] Requirements: NavigationMapReq / req_navigationMap ("display 2D map
    tiles fetched from an external provider (e.g. OpenStreetMap)"),
    L2SYS-REQ-02.01; ThreatAssessmentReq / req_threatAssessment
    ("receive and display radar detections"), L2SYS-REQ-02.03b (scaled,
    color-coded threat rings). Use cases UC_TacticalNavigation,
    UC_ThreatAssessment (subject = Tactical2DMap).
[a] Ports (TA) -> realization:
      i_OwnStateIn        -> slot on_ownship(OwnshipState)
      i_BeaconDataIn      -> slot on_beacon(RadarBeacon)
      i_2dTacticalMapOut  -> signal snapshot_ready(QPixmap)
      i_TileCachePathIn   -> method set_tile_cache_path(str)
      e_HttpMapTiles      -> HTTPS GET tile.openstreetmap.org (TileFetcher)
      mapGuiWorker.i_TileQueueOut  -> queue.Queue into the fetcher thread
      tileFetchWorker.i_TileReadyOut -> Qt signal tile_ready(z,x,y)

TA ACTION -> CODE MAPPING [b]
-----------------------------
  MapGuiWidget:
    processOwnshipState     -> MapGuiWidget.on_ownship
    processBeaconContacts   -> MapGuiWidget.on_beacon (+ staleness GC)
    calculateTileCoordinates-> deg2num / _visible_tiles
    render2dTacticalMap     -> MapGuiWidget.paintEvent
    push2dTacticalMap       -> MapWindow._push_snapshot (1 Hz QTimer)
  TileFetcher:
    requestExternalTiles    -> TileFetcher.run (HTTPS GET)
    cacheMapTiles           -> disk cache {cache}/{z}/{x}/{y}.png

DEVELOPER CHOICES [c] (flagged inline)
--------------------------------------
- Slippy-map (Web-Mercator) tile scheme and URL template: standard OSM
  convention, not in the model.
- OSM tile usage policy compliance: identifying User-Agent + disk cache
  (also satisfies cacheMapTiles) + max zoom 17.
- Beacon ring colors: FOE red, FRIEND green, UNKNOWN amber; ring radius
  = max_range_m at map scale; 'detected' beacons drawn filled.
- Map recenters on ownship each update (track-up OFF, north-up [c]);
  zoom via +/- keys and buttons, default z=11.
- Beacon staleness GC on repaint, same constants as common/messages.py.
"""

from __future__ import annotations

import math
import os
import queue
import time

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QPointF, QRectF
from PyQt5.QtGui import QPainter, QPixmap, QColor, QPen, QPolygonF, QFont
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel)

from flightsim.common import ports
from flightsim.common.messages import (OwnshipState, RadarBeacon,
                                       BEACON_STALE_TIMEOUT_S)

TILE_SIZE = 256
MIN_ZOOM, MAX_ZOOM = 3, 17          # [c] OSM policy: no bulk high-zoom
DEFAULT_ZOOM = 11

BEACON_COLORS = {                    # [c] classification -> color
    "FOE": QColor(220, 40, 40),
    "FRIEND": QColor(40, 180, 60),
    "UNKNOWN": QColor(230, 160, 20),
}


# ---------------------------------------------------------------------------
# calculateTileCoordinates [a] -- standard slippy-map math [c]
# ---------------------------------------------------------------------------

def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[float, float]:
    """WGS84 -> fractional tile coordinates (Web Mercator)."""
    lat_rad = math.radians(max(-85.0511, min(85.0511, lat_deg)))
    n = 2.0 ** zoom
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def meters_per_pixel(lat_deg: float, zoom: int) -> float:
    """Ground resolution at latitude/zoom [c: Web Mercator formula]."""
    return (156543.03392 * math.cos(math.radians(lat_deg))) / (2 ** zoom)


# ---------------------------------------------------------------------------
# TileFetcher [a: part def TileFetcher] -- background QThread
# ---------------------------------------------------------------------------

class TileFetcher(QThread):
    tile_ready = pyqtSignal(int, int, int)       # i_TileReadyOut (z, x, y)

    USER_AGENT = "FlightSimClaude/0.1 (training simulator; educational)"  # [c] OSM policy

    def __init__(self, cache_dir: str = "tile_cache"):
        super().__init__()
        self._queue: queue.Queue = queue.Queue()  # i_TileQueueIn
        self._cache_dir = cache_dir               # i_TileCachePathIn (default)
        self._pending: set[tuple[int, int, int]] = set()
        self._failed: dict[tuple[int, int, int], float] = {}
        self._alive = True

    # i_TileCachePathIn [a: port added to TA 2026-07-11]
    def set_tile_cache_path(self, path: str) -> None:
        self._cache_dir = path

    def cache_path(self, z: int, x: int, y: int) -> str:
        return os.path.join(self._cache_dir, str(z), str(x), f"{y}.png")

    # i_TileQueueIn [a] -- called from the GUI thread
    def request(self, z: int, x: int, y: int) -> None:
        key = (z, x, y)
        if key in self._pending:
            return
        if self._failed.get(key, 0) > time.monotonic() - 30:  # [c] backoff
            return
        self._pending.add(key)
        self._queue.put(key)

    def stop(self) -> None:
        self._alive = False
        self._queue.put(None)

    # requestExternalTiles + cacheMapTiles [a]
    def run(self) -> None:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = self.USER_AGENT
        while self._alive:
            item = self._queue.get()
            if item is None:
                break
            z, x, y = item
            path = self.cache_path(z, x, y)
            if not os.path.exists(path):
                url = ports.OSM_TILE_URL_TEMPLATE.format(z=z, x=x, y=y)
                try:
                    r = session.get(url, timeout=5)     # e_HttpMapTiles [a]
                    r.raise_for_status()
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(r.content)              # cacheMapTiles [a]
                except Exception:
                    self._failed[item] = time.monotonic()
                    self._pending.discard(item)
                    continue
            self._pending.discard(item)
            self.tile_ready.emit(z, x, y)               # i_TileReadyOut [a]


# ---------------------------------------------------------------------------
# MapGuiWidget [a: part def MapGuiWidget] -- main-thread Qt rendering
# ---------------------------------------------------------------------------

class MapGuiWidget(QWidget):
    def __init__(self, fetcher: TileFetcher):
        super().__init__()
        self._fetcher = fetcher                       # i_TileQueueOut side
        self._tiles: dict[tuple[int, int, int], QPixmap] = {}  # RAM cache [c]
        self.zoom = DEFAULT_ZOOM
        self.ownship: OwnshipState | None = None
        self.beacons: dict[str, tuple[RadarBeacon, float]] = {}
        self.setMinimumSize(480, 480)
        fetcher.tile_ready.connect(self._on_tile_ready)   # i_TileReadyIn [a]

    # ---- i_OwnStateIn [a] : processOwnshipState ----
    def on_ownship(self, tm: OwnshipState) -> None:
        self.ownship = tm
        self.update()

    # ---- i_BeaconDataIn [a] : processBeaconContacts ----
    def on_beacon(self, b: RadarBeacon) -> None:
        if b.active:
            self.beacons[b.beacon_id] = (b, time.monotonic())
        else:
            self.beacons.pop(b.beacon_id, None)          # tombstone [a]
        self.update()

    def _on_tile_ready(self, z: int, x: int, y: int) -> None:
        pm = QPixmap(self._fetcher.cache_path(z, x, y))
        if not pm.isNull():
            self._tiles[(z, x, y)] = pm
        self.update()

    def set_zoom(self, delta: int) -> None:
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom + delta))
        self.update()

    # ---- render2dTacticalMap [a] ----
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 34, 40))
        if self.ownship is None:
            p.setPen(Qt.white)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Waiting for ownship state...")
            p.end()
            return

        w, h = self.width(), self.height()
        cx, cy = deg2num(self.ownship.lat_deg, self.ownship.lon_deg, self.zoom)

        # visible tile range around the ownship-centered viewport
        half_w_tiles = w / 2 / TILE_SIZE
        half_h_tiles = h / 2 / TILE_SIZE
        n = 2 ** self.zoom
        x0 = int(math.floor(cx - half_w_tiles))
        x1 = int(math.floor(cx + half_w_tiles))
        y0 = int(math.floor(cy - half_h_tiles))
        y1 = int(math.floor(cy + half_h_tiles))

        for tx in range(x0, x1 + 1):
            for ty in range(max(0, y0), min(n - 1, y1) + 1):
                key = (self.zoom, tx % n, ty)
                px = w / 2 + (tx - cx) * TILE_SIZE
                py = h / 2 + (ty - cy) * TILE_SIZE
                pm = self._tiles.get(key)
                if pm is not None:
                    p.drawPixmap(int(px), int(py), pm)
                else:
                    p.fillRect(int(px), int(py), TILE_SIZE, TILE_SIZE,
                               QColor(45, 50, 58))
                    self._fetcher.request(*key)          # i_TileQueueOut [a]

        mpp = meters_per_pixel(self.ownship.lat_deg, self.zoom)

        # ---- beacons: scaled, color-coded rings [a: L2SYS-REQ-02.03b] ----
        now = time.monotonic()
        stale = [k for k, (_, t) in self.beacons.items()
                 if now - t > BEACON_STALE_TIMEOUT_S]
        for k in stale:
            del self.beacons[k]                          # GC [a: protocol]
        for b, _ in self.beacons.values():
            bx, by = deg2num(b.lat_deg, b.lon_deg, self.zoom)
            px = w / 2 + (bx - cx) * TILE_SIZE
            py = h / 2 + (by - cy) * TILE_SIZE
            color = BEACON_COLORS.get(b.classification,
                                      BEACON_COLORS["UNKNOWN"])
            r_px = b.max_range_m / mpp if mpp > 0 else 0
            pen = QPen(color, 2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if 2 < r_px < 4000:
                p.drawEllipse(QPointF(px, py), r_px, r_px)
            if b.detected:                               # [c] filled if hot
                p.setBrush(color)
            p.drawRect(QRectF(px - 6, py - 6, 12, 12))
            p.setPen(Qt.white)
            p.drawText(QPointF(px + 9, py - 9), b.beacon_id)

        # ---- ownship symbol: heading-rotated triangle [c] ----
        p.save()
        p.translate(w / 2, h / 2)
        p.rotate(self.ownship.heading_deg)
        tri = QPolygonF([QPointF(0, -14), QPointF(9, 12), QPointF(-9, 12)])
        p.setPen(QPen(Qt.white, 2))
        p.setBrush(QColor(60, 130, 255))
        p.drawPolygon(tri)
        p.restore()

        # ---- attribution + scale [c: OSM license requirement] ----
        p.setPen(Qt.white)
        p.setFont(QFont("Arial", 8))
        p.drawText(6, h - 8, "(c) OpenStreetMap contributors")
        p.drawText(6, 16, f"z{self.zoom}  {mpp * 100:.0f} m / 100 px")
        p.end()


# ---------------------------------------------------------------------------
# ThreadMapWindow assembly [a: part def ThreadMapWindow]
# ---------------------------------------------------------------------------

class MapWindow(QMainWindow):
    """Top-level tactical map window (spawned by PCS main thread [a])."""

    snapshot_ready = pyqtSignal(object)          # i_2dTacticalMapOut (QPixmap)

    def __init__(self, tile_cache_path: str = "tile_cache"):
        super().__init__()
        self.setWindowTitle("PCS - Tactical 2D Map")

        self.fetcher = TileFetcher(tile_cache_path)   # tileFetchWorker [a]
        self.fetcher.start()
        self.map_widget = MapGuiWidget(self.fetcher)  # mapGuiWorker [a]

        central = QWidget()
        root = QVBoxLayout(central)
        bar = QHBoxLayout()
        btn_in = QPushButton("+"); btn_out = QPushButton("-")
        btn_in.setFixedWidth(40); btn_out.setFixedWidth(40)
        btn_in.clicked.connect(lambda: self.map_widget.set_zoom(+1))
        btn_out.clicked.connect(lambda: self.map_widget.set_zoom(-1))
        self.lbl_info = QLabel("")
        bar.addWidget(btn_in); bar.addWidget(btn_out)
        bar.addWidget(self.lbl_info); bar.addStretch()
        root.addLayout(bar)
        root.addWidget(self.map_widget)
        self.setCentralWidget(central)

        # push2dTacticalMap [a] -- 1 Hz snapshot back to the cockpit [c rate]
        self._snap_timer = QTimer(self)
        self._snap_timer.timeout.connect(self._push_snapshot)
        self._snap_timer.start(1000)

    # ---- delegated ports [a: strict delegation bindings] ----
    def on_ownship(self, tm: OwnshipState) -> None:       # i_OwnStateIn
        self.map_widget.on_ownship(tm)
        self.lbl_info.setText(
            f"{tm.lat_deg:.4f}, {tm.lon_deg:.4f}  hdg {tm.heading_deg:.0f}")

    def on_beacon(self, b: RadarBeacon) -> None:          # i_BeaconDataIn
        self.map_widget.on_beacon(b)

    def set_tile_cache_path(self, path: str) -> None:     # i_TileCachePathIn
        self.fetcher.set_tile_cache_path(path)

    def _push_snapshot(self) -> None:                     # i_2dTacticalMapOut
        if self.isVisible():
            self.snapshot_ready.emit(self.map_widget.grab())

    def closeEvent(self, event) -> None:
        self._snap_timer.stop()
        self.fetcher.stop()
        self.fetcher.wait(2000)
        event.accept()