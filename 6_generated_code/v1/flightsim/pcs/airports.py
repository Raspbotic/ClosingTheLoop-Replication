"""
flightsim/pcs/airports.py
=========================
Configurable airport table used for the ground-start initial condition and
for the OTW launch location.

TRACEABILITY
------------
[a] L2SYS-REQ-01.04: "The system shall initialize the Virtual Ownship on
    ground at a user-selected airport from a configurable airport list."
    (L2SYS-FCN-01.4; allocated to PilotControlStation + VirtualOwnship.)
[a] TA action ssfSelectAirport (fsim_TA_PCS_ThreadFlightSim_UIM.sysml):
    "select the active airport (ICAO) from a configurable list; sets the
    ownship ground-start initial condition and the OTW launch location."
[a: Decision D13] "Airports. Seed table LIML, LIMC, KSFO, EDDF (ICAO,
    name, lat/lon, runway heading, elevation); optional
    airports_local.json per-ICAO partial overrides, corrupt file
    survivable."

NOTE (specification observation, not a change): the reference screenshot
Desired_UIUX_capture.JPG shows "Munich (EDDM)" in the Mission Control
combo. D13 fixes the seed table to LIML/LIMC/KSFO/EDDF, so the seed table
follows D13; EDDM (or any other field) can be added by the operator through
airports_local.json without a code change, which is what "configurable"
in 01.04 requires.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

#: [c] Name of the optional per-ICAO override file; D13 names the file but
#: not its location, so it is looked up next to this package and in the CWD.
LOCAL_OVERRIDE_FILENAME = "airports_local.json"


@dataclass(frozen=True)
class Airport:
    """One airport entry. Field set fixed by D13.

    [a: D13] "(ICAO, name, lat/lon, runway heading, elevation)"
    """

    icao: str
    name: str
    lat_deg: float
    lon_deg: float
    runway_heading_deg: float
    elevation_m: float

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------
# Seed table [a: D13]
# --------------------------------------------------------------------------

_SEED: List[Airport] = [
    # Milano Linate, runway 17/35 -> ground start on 17 (173 deg true).
    Airport("LIML", "Milano Linate", 45.4451, 9.2767, 173.0, 108.0),
    # Milano Malpensa, runway 35R/17L -> ground start on 35R (350 deg).
    Airport("LIMC", "Milano Malpensa", 45.6306, 8.7281, 350.0, 234.0),
    # San Francisco International, runway 28R (284 deg).
    Airport("KSFO", "San Francisco Intl", 37.6189, -122.3750, 284.0, 4.0),
    # Frankfurt am Main, runway 25C (249 deg).
    Airport("EDDF", "Frankfurt am Main", 50.0333, 8.5706, 249.0, 111.0),
]

#: [a: D13] the four fields that a local override may replace per ICAO.
_OVERRIDABLE = ("name", "lat_deg", "lon_deg", "runway_heading_deg",
                "elevation_m")


def _candidate_paths(explicit: Optional[str]) -> List[str]:
    if explicit:
        return [explicit]
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(os.getcwd(), LOCAL_OVERRIDE_FILENAME),
        os.path.join(here, LOCAL_OVERRIDE_FILENAME),
        os.path.join(os.path.dirname(os.path.dirname(here)),
                     LOCAL_OVERRIDE_FILENAME),
    ]


class AirportTable:
    """The configurable airport list of 01.04.

    [a: D13] the seed table is always present; airports_local.json may
    override individual fields of an existing ICAO or add a new one; a
    corrupt file must be survivable (the seed table stays usable).
    """

    def __init__(self, override_path: Optional[str] = None,
                 load_overrides: bool = True):
        self._airports: Dict[str, Airport] = {a.icao: a for a in _SEED}
        self.override_error: Optional[str] = None
        self.override_source: Optional[str] = None
        if load_overrides:
            self._load_overrides(override_path)

    # -- overrides -------------------------------------------------------
    def _load_overrides(self, explicit: Optional[str]) -> None:
        for path in _candidate_paths(explicit):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError("top level must be an object keyed by ICAO")
                self._apply_overrides(data)
                self.override_source = path
            except Exception as exc:            # [a: D13] corrupt file survivable
                self.override_error = f"{path}: {exc}"
            return

    def _apply_overrides(self, data: Dict[str, object]) -> None:
        for icao, patch in data.items():
            icao = str(icao).upper()
            if not isinstance(patch, dict):
                raise ValueError(f"{icao}: entry must be an object")
            base = self._airports.get(icao)
            if base is None:
                # [a: D13] "per-ICAO partial overrides" implies additions are
                # possible; a NEW field needs the full record [b].
                missing = [k for k in _OVERRIDABLE if k not in patch]
                if missing:
                    raise ValueError(
                        f"{icao}: new airport needs {missing}")
                self._airports[icao] = Airport(
                    icao=icao, name=str(patch["name"]),
                    lat_deg=float(patch["lat_deg"]),
                    lon_deg=float(patch["lon_deg"]),
                    runway_heading_deg=float(patch["runway_heading_deg"]),
                    elevation_m=float(patch["elevation_m"]))
                continue
            fields = base.as_dict()
            for key, value in patch.items():
                if key not in _OVERRIDABLE:
                    raise ValueError(f"{icao}: unknown field {key!r}")
                fields[key] = str(value) if key == "name" else float(value)
            self._airports[icao] = Airport(**fields)  # type: ignore[arg-type]

    # -- access ----------------------------------------------------------
    def icaos(self) -> List[str]:
        """ICAO codes in a stable order (seed order, then additions)."""
        seed_order = [a.icao for a in _SEED if a.icao in self._airports]
        extra = sorted(set(self._airports) - set(seed_order))
        return seed_order + extra

    def get(self, icao: str) -> Airport:
        try:
            return self._airports[str(icao).upper()]
        except KeyError:
            raise KeyError(f"unknown airport {icao!r}; "
                           f"known: {', '.join(self.icaos())}") from None

    def all(self) -> List[Airport]:
        return [self._airports[i] for i in self.icaos()]

    def default(self) -> Airport:
        """[b] First seed entry; D13 fixes the table, not the default."""
        return self._airports[self.icaos()[0]]

    def __len__(self) -> int:
        return len(self._airports)

    def __contains__(self, icao: object) -> bool:
        return str(icao).upper() in self._airports


#: Convenience singleton used by the PCS and the physics thread.
DEFAULT_TABLE = AirportTable()

__all__ = ["Airport", "AirportTable", "DEFAULT_TABLE",
           "LOCAL_OVERRIDE_FILENAME"]
