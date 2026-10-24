"""
flightsim.pcs.airports
======================
Airport list: single source of truth for BOTH the ownship ground-start
initial condition AND the FlightGear OTW launch location.

TRACEABILITY
------------
[a-pending: L2SYS-REQ-01.04/5, agreed 2026-07-11] "initialize the ownship
on ground at a user-selected airport from a configurable airport list;
selection changeable only during flight-freeze" / "OTW visual system
launched with the aircraft model and the currently selected airport,
consistent with the ownship initialization."
[a-pending: ssfSelectAirport action + extended ssfLaunchOtwView doc in
fsim_TA_PCS_ThreadFlightSim_UIM.sysml]

DATA [c]
--------
Runway-threshold coordinates, field elevations, and runway headings below
are developer-entered approximations adequate for simulation start (the
aircraft spawns at the threshold aligned with the runway). Extend/correct
this table at will -- it is the configurable list the requirement names.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Airport:
    icao: str
    name: str
    lat_deg: float          # runway threshold
    lon_deg: float
    elev_m: float           # field elevation MSL
    rwy_heading_deg: float  # runway true heading at spawn
    rwy: str                # runway designator (informational)


# [c] seed list; extend at will
AIRPORTS: dict[str, Airport] = {a.icao: a for a in [
    Airport("LIML", "Milano Linate",   45.4494, 9.2783,  103.0, 356.0, "36"),
    Airport("LIMC", "Milano Malpensa", 45.6169, 8.7361,  234.0, 354.0, "35L"),
    Airport("KSFO", "San Francisco",   37.6113, -122.3579, 4.0,  283.0, "28R"),
    Airport("EDDF", "Frankfurt Main",  50.0333, 8.5344,  111.0, 249.0, "25C"),
]}

DEFAULT_ICAO = "LIML"   # [c] default selection at process start


# ---------------------------------------------------------------------------
# Local fine-tuning [b: bug fix 2026-07-12 -- seed coordinates are
# approximations; runway alignment needs per-installation tuning].
#
# Create 'airports_local.json' in the project root to override any field
# per ICAO (partial overrides allowed), e.g.:
#   { "LIML": { "lat_deg": 45.44912, "lon_deg": 9.27835,
#               "rwy_heading_deg": 355.6, "elev_m": 103.6 } }
#
# CAPTURE PROCEDURE: launch PCS + FlightGear, use SSF "SET POSITION" /
# taxi until the OTW view sits exactly on the runway threshold, read
# Lat/Lon from the Ownship State panel (and FG's position dialog), paste
# into the JSON, restart.
# ---------------------------------------------------------------------------
import json as _json
import os as _os

_OVERRIDE_FILE = "airports_local.json"
if _os.path.exists(_OVERRIDE_FILE):
    try:
        with open(_OVERRIDE_FILE) as _f:
            _ov = _json.load(_f)
        for _icao, _fields in _ov.items():
            if _icao in AIRPORTS:
                _base = AIRPORTS[_icao].__dict__ | _fields
                AIRPORTS[_icao] = Airport(**_base)
        print(f"[airports] local overrides applied: {sorted(_ov)}")
    except Exception as _e:      # never let a bad JSON kill the PCS [c]
        print(f"[airports] WARNING: {_OVERRIDE_FILE} ignored ({_e})")