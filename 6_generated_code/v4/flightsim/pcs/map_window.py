"""ThreadMapWindow — Tactical2DMap. [a: fsim_TA_PCS_ThreadMapWindow.sysml#ThreadMapWindow] (map_window.py)

mapGuiWorker : MapGuiWidget (GUI thread): processOwnshipState, processBeaconContacts,
calculateTileCoordinates, render2dTacticalMap, push2dTacticalMap.
tileFetchWorker : TileFetcher (QThread): requestExternalTiles, cacheMapTiles.
[a: L2SYS-REQ-02.01] [a: L2SYS-REQ-02.03b] [a: Behaviour B14]
Ports: i_OwnStateIn, i_BeaconDataIn (slots), i_2dTacticalMapOut (signal), i_TileCachePathIn
(constructor/slot), e_HttpMapTiles (HTTPS tile.openstreetmap.org).
"""
from __future__ import annotations

import math
import os
import queue
import urllib.request

from PyQt5 import QtCore, QtGui, QtWidgets

from flightsim.common import ports

TILE = 256
ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAULT = 3, 17, 12  # [c] default matches the screenshot "Zoom: 12"
ZOOM_FIT_MAX = 14  # [c] auto-fit never zooms in beyond this
FIT_MARGIN_PX = 12
RECENTER_MARGIN = 0.15  # [c] re-centre when the ownship comes within 15 % of the map edge
USER_AGENT = "fsim-pcs-tactical-map/1.0"      # [c] OSM tile usage policy requires a User-Agent
RING_COLORS = {"FOE": QtGui.QColor(255, 40, 40), "FRIEND": QtGui.QColor(40, 220, 60),
               "UNKNOWN": QtGui.QColor(255, 210, 0)}  # [c] colour code (02.03b names color-coding, not colours)


# ------------------------------------------------------------ calculateTileCoordinates
def latlon_to_tile_xy(lat, lon, z):
    """Fractional slippy-map tile coordinates (Web Mercator)."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def meters_per_pixel(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def tile_url(z, x, y):
    return f"https://{ports.OSM_TILE_HOST}/{z}/{x}/{y}.png"  # [a: fsim_TA_PCS_ThreadMapWindow.sysml#TileFetcher::e_HttpMapTiles]


def _http_fetch(url, timeout=5.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class TileFetcher(QtCore.QThread):
    """Background worker. i_TileQueueIn = request(); i_TileReadyOut = tile_ready signal."""
    tile_ready = QtCore.pyqtSignal(int, int, int, str)
    tile_failed = QtCore.pyqtSignal(int, int, int, str)

    def __init__(self, cache_dir, fetch=_http_fetch, parent=None):
        super().__init__(parent)
        self.cache_dir = cache_dir  # i_TileCachePathIn
        self.fetch = fetch
        self.q = queue.Queue()
        self._stop = False
        self.requests = 0

    def request(self, z, x, y):
        self.q.put((z, x, y))

    def path_for(self, z, x, y):
        return os.path.join(self.cache_dir, str(z), str(x), f"{y}.png")

    def run(self):
        while not self._stop:
            try:
                z, x, y = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            p = self.path_for(z, x, y)
            if os.path.exists(p):  # cacheMapTiles: disk cache first
                self.tile_ready.emit(z, x, y, p)
                continue
            try:  # requestExternalTiles
                self.requests += 1
                data = self.fetch(tile_url(z, x, y))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
                self.tile_ready.emit(z, x, y, p)
            except Exception as e:
                self.tile_failed.emit(z, x, y, str(e))

    def stop(self):
        self._stop = True
        self.wait(2000)


class MapGuiWidget(QtWidgets.QWidget):
    """render2dTacticalMap on the GUI thread."""
    i_2dTacticalMapOut = QtCore.pyqtSignal(str)  # push2dTacticalMap -> ThreadFlightSim.i_2dTileIn
    i_TileQueueOut = QtCore.pyqtSignal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.zoom = ZOOM_DEFAULT
        self.own = None          # dict with lat_deg, lon_deg, heading_deg
        self.beacons = {}        # beacon_id -> RadarBeacon
        self.tiles = {}          # (z,x,y) -> QPixmap
        self.pending = set()
        self.failed = set()
        self._reported_offline = False
        self._fit_pending = False
        self.view_center = None  # ground-fixed map centre (lat, lon)

    # processOwnshipState (i_OwnStateIn)
    def set_ownship(self, t):
        """The map is fixed to the ground: tiles and beacons stay where they are and the ownship
        symbol moves across them. The view re-centres on the ownship only when it nears the edge.
        Practitioner decision (field defect D-6) superseding B14 "ownship symbol at the centre"."""
        self.own = dict(lat_deg=t["lat_deg"], lon_deg=t["lon_deg"], heading_deg=t["heading_deg"])
        if self.view_center is None:
            self.view_center = (self.own["lat_deg"], self.own["lon_deg"])
        if self._fit_pending:
            self.fit_zoom()
        elif not self._ownship_inside():
            self.recenter()
        self.request_visible_tiles()
        self.update()

    def _ownship_inside(self):
        x, y = self.geo_to_px(self.own["lat_deg"], self.own["lon_deg"])
        mx, my = self.width() * RECENTER_MARGIN, self.height() * RECENTER_MARGIN
        return mx <= x <= self.width() - mx and my <= y <= self.height() - my

    def recenter(self):
        if self.own:
            self.view_center = (self.own["lat_deg"], self.own["lon_deg"])
            self.request_visible_tiles()
            self.update()

    def mouseDoubleClickEvent(self, ev):
        self.recenter()

    # processBeaconContacts (i_BeaconDataIn)
    def set_beacons(self, beacons):
        """Beacons are drawn at their own ground position (fixed where the SEM placed them).
        When a new beacon appears the zoom is set so every ring and the ownship are in view;
        otherwise a ring larger than the view (R_max ~39 km vs ~8 km at zoom 12) fills the map
        and looks attached to the ownship. [c] auto-fit; the user can re-zoom with the wheel."""
        new = set(beacons) - set(self.beacons)
        self.beacons = dict(beacons)
        if new:
            self._fit_pending = True
            if self.own:
                self.fit_zoom()
        self.update()

    def fit_zoom(self):
        """Centre the ground-fixed view on the box holding the ownship and every ring, and take
        the highest zoom (<= ZOOM_FIT_MAX) at which that box fits."""
        self._fit_pending = False
        if not self.own or not self.beacons:
            return
        lat0, lon0 = self.own["lat_deg"], self.own["lon_deg"]
        kx = 111320.0 * math.cos(math.radians(lat0))
        ky = 110540.0
        xs, ys = [0.0], [0.0]  # metres east / north of the ownship
        for b in self.beacons.values():
            bx, by = (b.lon_deg - lon0) * kx, (b.lat_deg - lat0) * ky
            xs += [bx - b.max_range_m, bx + b.max_range_m]
            ys += [by - b.max_range_m, by + b.max_range_m]
        cx_m, cy_m = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        self.view_center = (lat0 + cy_m / ky, lon0 + cx_m / kx)
        need_x, need_y = (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2
        half_w = max(1.0, self.width() / 2 - FIT_MARGIN_PX)
        half_h = max(1.0, self.height() / 2 - FIT_MARGIN_PX)
        z = ZOOM_FIT_MAX
        while z > ZOOM_MIN and (need_x / meters_per_pixel(lat0, z) > half_w or need_y / meters_per_pixel(lat0, z) > half_h):
            z -= 1
        self.zoom = z
        self.request_visible_tiles()

    def sizeHint(self):
        return QtCore.QSize(520, 480)

    def center(self):
        return self.view_center

    def visible_tiles(self):
        c = self.center()
        if c is None:
            return []
        cx, cy = latlon_to_tile_xy(c[0], c[1], self.zoom)
        hw, hh = self.width() / 2 / TILE, self.height() / 2 / TILE
        n = 2 ** self.zoom
        out = []
        for tx in range(int(math.floor(cx - hw)), int(math.floor(cx + hw)) + 1):
            for ty in range(int(math.floor(cy - hh)), int(math.floor(cy + hh)) + 1):
                if 0 <= ty < n:
                    out.append((self.zoom, tx % n, ty))
        return out

    def request_visible_tiles(self):
        for key in self.visible_tiles():
            if key not in self.tiles and key not in self.pending and key not in self.failed:
                self.pending.add(key)
                self.i_TileQueueOut.emit(*key)

    def on_tile_ready(self, z, x, y, path):  # i_TileReadyIn
        self.pending.discard((z, x, y))
        pm = QtGui.QPixmap(path)
        if not pm.isNull():
            self.tiles[(z, x, y)] = pm
            self.i_2dTacticalMapOut.emit(f"tile {z}/{x}/{y}")
            self.update()

    def on_tile_failed(self, z, x, y, err):
        self.pending.discard((z, x, y))
        self.failed.add((z, x, y))
        if not self._reported_offline:
            self._reported_offline = True
            self.i_2dTacticalMapOut.emit(f"MAP TILES UNAVAILABLE: {err}")

    def wheelEvent(self, ev):
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom + (1 if ev.angleDelta().y() > 0 else -1)))
        self.request_visible_tiles()
        self.update()

    def geo_to_px(self, lat, lon):
        c = self.center()
        cx, cy = latlon_to_tile_xy(c[0], c[1], self.zoom)
        x, y = latlon_to_tile_xy(lat, lon, self.zoom)
        return self.width() / 2 + (x - cx) * TILE, self.height() / 2 + (y - cy) * TILE

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(40, 44, 48))
        c = self.center()
        if c is None:
            p.setPen(QtGui.QColor(160, 160, 160))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "WAITING FOR OWNSHIP STATE")
            p.end()
            return
        cx, cy = latlon_to_tile_xy(c[0], c[1], self.zoom)
        n = 2 ** self.zoom
        for (z, x, y) in self.visible_tiles():
            pm = self.tiles.get((z, x, y))
            # unwrap x relative to centre for drawing
            dx = x - math.floor(cx)
            if dx > n / 2: dx -= n
            if dx < -n / 2: dx += n
            px = self.width() / 2 + (math.floor(cx) + dx - cx) * TILE
            py = self.height() / 2 + (y - cy) * TILE
            if pm is not None:
                p.drawPixmap(int(round(px)), int(round(py)), pm)
            else:
                p.setPen(QtGui.QColor(70, 74, 78))
                p.drawRect(int(px), int(py), TILE, TILE)
        # threat rings: radius R_max scaled by map resolution [a: L2SYS-REQ-02.03b]
        mpp = meters_per_pixel(c[0], self.zoom)
        for b in self.beacons.values():
            col = QtGui.QColor(RING_COLORS.get(b.classification, RING_COLORS["UNKNOWN"]))
            bx, by = self.geo_to_px(b.lat_deg, b.lon_deg)
            r = b.max_range_m / mpp
            pen = QtGui.QPen(col, 4 if b.detected else 2)
            p.setPen(pen)
            fill = QtGui.QColor(col); fill.setAlpha(60 if b.detected else 20)
            p.setBrush(fill)
            p.drawEllipse(QtCore.QPointF(bx, by), r, r)
            p.setBrush(col)
            p.drawRect(QtCore.QRectF(bx - 4, by - 4, 8, 8))
            p.drawText(QtCore.QPointF(bx + 7, by - 7), f"{ {'FOE': 'FOE', 'FRIEND': 'FRD', 'UNKNOWN': 'UNK'}.get(b.classification, '?')}{b.beacon_id}")
        # ownship symbol at its own ground position (D-6)
        ox, oy = self.geo_to_px(self.own["lat_deg"], self.own["lon_deg"])
        p.save()
        p.translate(ox, oy)
        p.rotate(self.own["heading_deg"])
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 2))
        p.setBrush(QtGui.QColor(255, 230, 0))
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(0, -12), QtCore.QPointF(8, 10), QtCore.QPointF(0, 5), QtCore.QPointF(-8, 10)]))
        p.restore()
        # zoom / scale box (screenshot)
        bar_px = 100
        bar_km = bar_px * mpp / 1000.0
        box = QtCore.QRectF(8, self.height() - 48, 140, 40)
        p.setBrush(QtGui.QColor(0, 0, 0, 170)); p.setPen(QtCore.Qt.NoPen)
        p.drawRect(box)
        p.setPen(QtGui.QColor(255, 255, 255))
        p.drawText(QtCore.QPointF(box.left() + 40, box.top() + 14), f"Zoom: {self.zoom}")
        p.drawText(QtCore.QPointF(box.left() + 40, box.top() + 27), f"{bar_km:.2f} km")
        p.drawLine(QtCore.QPointF(box.left() + 20, box.top() + 33), QtCore.QPointF(box.left() + 20 + bar_px, box.top() + 33))
        p.end()


class ThreadMapWindow(QtWidgets.QMainWindow):
    """Top-level window 'PCS - Tactical GPS Map (Live)' assembling both parts (strict delegation)."""

    def __init__(self, cache_dir, fetch=_http_fetch):
        super().__init__()
        self.setWindowTitle("PCS - Tactical GPS Map (Live)")
        self.mapGuiWorker = MapGuiWidget()
        self.tileFetchWorker = TileFetcher(cache_dir, fetch=fetch)
        self.setCentralWidget(self.mapGuiWorker)
        # bind mapGuiWorker.i_TileQueueOut = tileFetchWorker.i_TileQueueIn
        self.mapGuiWorker.i_TileQueueOut.connect(self.tileFetchWorker.request)
        # bind tileFetchWorker.i_TileReadyOut = mapGuiWorker.i_TileReadyIn
        self.tileFetchWorker.tile_ready.connect(self.mapGuiWorker.on_tile_ready)
        self.tileFetchWorker.tile_failed.connect(self.mapGuiWorker.on_tile_failed)
        self.i_2dTacticalMapOut = self.mapGuiWorker.i_2dTacticalMapOut
        self.tileFetchWorker.start()
        self.resize(540, 520)

    # boundary ports delegate to mapGuiWorker
    def i_OwnStateIn(self, t):
        self.mapGuiWorker.set_ownship(t)

    def i_BeaconDataIn(self, beacons):
        self.mapGuiWorker.set_beacons(beacons)

    def i_TileCachePathIn(self, path):
        self.tileFetchWorker.cache_dir = path

    def shutdown(self):
        self.tileFetchWorker.stop()
        self.close()
