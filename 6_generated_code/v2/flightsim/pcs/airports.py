"""Configurable airport list. [a: L2SYS-REQ-01.04] [a: D13]

L2SYS-REQ-01.04: "The system shall initialize the Virtual Ownship on ground at a
user-selected airport from a configurable airport list."

[a: D13, provisional] gives the seed table and the override mechanism:
"Optional airports_local.json with per-ICAO partial overrides: an unmentioned
key keeps its seed value. Tolerated without failing the load: a corrupt file, a
corrupt value inside a valid file, and a partial row for an unknown ICAO
(ignored). A complete row for an unknown ICAO is added."

Qt-free and filesystem-optional: `load_airports` takes the override path as a
parameter, so the suite redirects it into tmp_path. [a: D18]
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Iterable, Mapping

#: The keys a complete row must have. A row missing any of them is partial.
#: [b: D13] -- D13 distinguishes a "partial row" from a "complete row" for an
#: unknown ICAO but does not enumerate the keys; the seed table's own columns
#: are the enumeration.
REQUIRED_KEYS = ("name", "lat_deg", "lon_deg", "runway_hdg_deg", "elevation_m")


@dataclass(frozen=True)
class Airport:
    """One row of the D13 table. Units are D13's: degrees and metres."""

    icao: str
    name: str
    lat_deg: float
    lon_deg: float
    runway_hdg_deg: float
    elevation_m: float

    def label(self) -> str:
        """What the Mission Control combo box shows. [b: D14, screenshot]"""
        return f"{self.name} ({self.icao})"


#: [a: D13] The seed table, transcribed exactly.
#:
#: | ICAO | Name | Lat | Lon | Runway hdg | Elevation (m) |
#: | LIML | Milano Linate      | 45.4451 |    9.2767 | 176.0 | 107.6 |
#: | LIMC | Milano Malpensa    | 45.6306 |    8.7281 | 350.0 | 234.1 |
#: | KSFO | San Francisco Intl | 37.6188 | -122.3750 | 284.0 |   4.0 |
#: | EDDF | Frankfurt am Main  | 50.0333 |    8.5706 | 250.0 | 111.0 |
SEED_AIRPORTS: tuple[Airport, ...] = (
    Airport("LIML", "Milano Linate", 45.4451, 9.2767, 176.0, 107.6),
    Airport("LIMC", "Milano Malpensa", 45.6306, 8.7281, 350.0, 234.1),
    Airport("KSFO", "San Francisco Intl", 37.6188, -122.3750, 284.0, 4.0),
    Airport("EDDF", "Frankfurt am Main", 50.0333, 8.5706, 250.0, 111.0),
)

#: [a: D13] the override file's name.
OVERRIDE_FILENAME = "airports_local.json"

#: The airport selected when nothing else is. [c] -- what would settle it: a
#: stated default airport; D13 gives a table but no default. The first seed row
#: is used, which is stable under a re-ordering only because the table is
#: written in D13's order.
DEFAULT_ICAO = SEED_AIRPORTS[0].icao


def _coerce(value: object, key: str) -> float | str | None:
    """Coerce one override value, or return None if it is corrupt.

    [a: D13] "Tolerated without failing the load: ... a corrupt value inside a
    valid file". A corrupt value is dropped and the seed value survives; the
    load does not fail and the rest of the row still applies.
    """
    if key == "name":
        return value if isinstance(value, str) and value else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if key == "lat_deg" and not (-90.0 <= v <= 90.0):
        return None
    if key == "lon_deg" and not (-180.0 <= v <= 180.0):
        return None
    return v


def apply_overrides(
    seed: Iterable[Airport], overrides: Mapping[str, object]
) -> tuple[Airport, ...]:
    """Merge per-ICAO partial overrides onto the seed table. [a: D13]

    * a key the override does not mention keeps its seed value
    * a corrupt value is ignored, the rest of the row still applies
    * a **partial** row for an unknown ICAO is ignored
    * a **complete** row for an unknown ICAO is added
    """
    by_icao: dict[str, Airport] = {a.icao: a for a in seed}
    order = list(by_icao)

    if not isinstance(overrides, Mapping):
        return tuple(by_icao[i] for i in order)

    for icao, row in overrides.items():
        if not isinstance(icao, str) or not isinstance(row, Mapping):
            continue
        key = icao.upper()
        clean: dict[str, object] = {}
        for k in REQUIRED_KEYS:
            if k in row:
                v = _coerce(row[k], k)
                if v is not None:
                    clean[k] = v

        if key in by_icao:
            # partial override of a known airport: unmentioned keys keep their
            # seed value, which `replace` gives for free
            by_icao[key] = replace(by_icao[key], **clean)  # type: ignore[arg-type]
        else:
            if set(clean) >= set(REQUIRED_KEYS):
                by_icao[key] = Airport(icao=key, **clean)  # type: ignore[arg-type]
                order.append(key)
            # else: a partial row for an unknown ICAO is ignored [a: D13]

    return tuple(by_icao[i] for i in order)


def read_overrides(path: str | None) -> dict:
    """Read airports_local.json, tolerating a corrupt or absent file. [a: D13]"""
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        # [a: D13] "Tolerated without failing the load: a corrupt file"
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def load_airports(
    directory: str | None = None, override_path: str | None = None
) -> tuple[Airport, ...]:
    """The configured airport list. [a: L2SYS-REQ-01.04] [a: D13]

    `directory` is where airports_local.json is looked for; `override_path`
    names the file outright. Both are parameters so the suite can redirect them
    into tmp_path and so nothing is read from the host by default. [a: D18]
    With neither given, the seed table is returned and no file is read.
    """
    if override_path is None and directory is not None:
        override_path = os.path.join(directory, OVERRIDE_FILENAME)
    return apply_overrides(SEED_AIRPORTS, read_overrides(override_path))


def find(airports: Iterable[Airport], icao: str) -> Airport | None:
    key = (icao or "").upper()
    for a in airports:
        if a.icao == key:
            return a
    return None
