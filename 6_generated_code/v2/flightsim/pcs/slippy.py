"""Web-Mercator slippy-map arithmetic -- Qt-free. [a: D24] [a: R-7]

[a: D24] "256-pixel Web-Mercator tiles; default zoom 12; zoom clamped 2-18."

Separated from `map_window.py` so the coordinate maths is verifiable without a
display, per R-7's "keep the model layer of both free of Qt". Nothing here
imports Qt, and nothing here touches the network.

Realizes `MapGuiWidget.calculateTileCoordinates`.
[a: fsim_TA_PCS_ThreadMapWindow.sysml]
"""

from __future__ import annotations

import math

#: [a: D24] "256-pixel ... tiles"
TILE_SIZE_PX = 256

#: [a: D24] "default zoom 12"
DEFAULT_ZOOM = 12

#: [a: D24] "zoom clamped 2-18"
MIN_ZOOM = 2
MAX_ZOOM = 18

#: The Web-Mercator projection is only defined up to about +-85.05113 degrees;
#: beyond it `tan` diverges. [c] -- a property of the projection, not of a
#: supplied artifact.
MAX_LATITUDE_DEG = 85.05112877980659

#: Equatorial circumference of the Earth in the Web-Mercator sphere, metres.
#: 2 * pi * 6378137 (the WGS-84 semi-major axis, which Web-Mercator uses).
#: [c] -- the projection's own constant.
EARTH_CIRCUMFERENCE_M = 2.0 * math.pi * 6378137.0


def clamp_zoom(z: int) -> int:
    """[a: D24] "zoom clamped 2-18"."""
    return max(MIN_ZOOM, min(MAX_ZOOM, int(z)))


def clamp_latitude(lat_deg: float) -> float:
    return max(-MAX_LATITUDE_DEG, min(MAX_LATITUDE_DEG, lat_deg))


def lonlat_to_tile_xy(lon_deg: float, lat_deg: float, zoom: int
                      ) -> tuple[float, float]:
    """Fractional tile coordinates. [a: D24]

        n = 2 ** zoom
        x = (lon + 180) / 360 * n
        y = (1 - ln(tan(lat) + sec(lat)) / pi) / 2 * n

    Fractional so a position inside a tile is representable; `int()` of the
    result is the tile index.
    """
    z = clamp_zoom(zoom)
    n = 2.0 ** z
    lat = math.radians(clamp_latitude(lat_deg))
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat) + 1.0 / math.cos(lat)) / math.pi) / 2.0 * n
    return x, y


def lonlat_to_tile(lon_deg: float, lat_deg: float, zoom: int
                   ) -> tuple[int, int]:
    """The integer tile a position falls in. [a: D24]"""
    x, y = lonlat_to_tile_xy(lon_deg, lat_deg, zoom)
    n = int(2.0 ** clamp_zoom(zoom))
    return (max(0, min(n - 1, int(math.floor(x)))),
            max(0, min(n - 1, int(math.floor(y)))))


def tile_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    """The north-west corner of a tile. The inverse of `lonlat_to_tile_xy`."""
    z = clamp_zoom(zoom)
    n = 2.0 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lon, lat


def ground_resolution_m_per_px(lat_deg: float, zoom: int) -> float:
    """Metres per pixel at a latitude and zoom. [b: D24]

        C * cos(lat) / (TILE_SIZE_PX * 2 ** zoom)

    This is what converts a beacon's `max_range_m` into a ring radius in
    pixels, so the rings are **scaled** as L2SYS-REQ-02.03b requires.
    """
    z = clamp_zoom(zoom)
    lat = math.radians(clamp_latitude(lat_deg))
    return (EARTH_CIRCUMFERENCE_M * math.cos(lat)
            / (TILE_SIZE_PX * (2.0 ** z)))


def metres_to_pixels(metres: float, lat_deg: float, zoom: int) -> float:
    """[a: L2SYS-REQ-02.03b] "scaled ... rings"."""
    res = ground_resolution_m_per_px(lat_deg, zoom)
    if res <= 0.0:
        return 0.0
    return metres / res


def pixel_offset(
    lon_deg: float, lat_deg: float,
    centre_lon_deg: float, centre_lat_deg: float,
    zoom: int,
) -> tuple[float, float]:
    """Pixel offset of a position from the map centre. [b: D24]

    Positive x is east, positive y is **down** (south), which is the screen
    convention Qt paints in.
    """
    cx, cy = lonlat_to_tile_xy(centre_lon_deg, centre_lat_deg, zoom)
    px, py = lonlat_to_tile_xy(lon_deg, lat_deg, zoom)
    return ((px - cx) * TILE_SIZE_PX, (py - cy) * TILE_SIZE_PX)


def tiles_covering(
    centre_lon_deg: float, centre_lat_deg: float, zoom: int,
    width_px: int, height_px: int,
) -> list[tuple[int, int]]:
    """Every tile index needed to fill a viewport. [b: D24]"""
    z = clamp_zoom(zoom)
    n = int(2.0 ** z)
    cx, cy = lonlat_to_tile_xy(centre_lon_deg, centre_lat_deg, z)
    half_w = width_px / (2.0 * TILE_SIZE_PX)
    half_h = height_px / (2.0 * TILE_SIZE_PX)
    x0 = int(math.floor(cx - half_w))
    x1 = int(math.floor(cx + half_w))
    y0 = int(math.floor(cy - half_h))
    y1 = int(math.floor(cy + half_h))
    out: list[tuple[int, int]] = []
    for ty in range(y0, y1 + 1):
        if ty < 0 or ty >= n:
            continue
        for tx in range(x0, x1 + 1):
            out.append((tx % n, ty))
    return out


def scale_bar_metres(lat_deg: float, zoom: int, bar_px: int = 100) -> float:
    """Ground distance a scale bar of `bar_px` represents. [b: reference
    screenshot, which shows a scale bar and a "Zoom: 12" caption]"""
    return ground_resolution_m_per_px(lat_deg, zoom) * bar_px
