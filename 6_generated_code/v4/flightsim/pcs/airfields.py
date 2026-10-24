"""Airfield seed table and local overrides. [a: Behaviour B11] [a: L2SYS-REQ-01.04]

Threshold coordinates, true runway heading and threshold elevation: [c] transcribed from
SkyVector runway data (Sep 2026); settled by the published AIP.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, asdict

FT = 0.3048


@dataclass
class Airfield:
    icao: str
    name: str
    lat_deg: float      # runway threshold used for the ground start [a: Behaviour B6]
    lon_deg: float
    runway_hdg_deg: float  # true
    elevation_m: float


SEED = {  # [a: Behaviour B11] ETSI, EDDM, EDDN
    "ETSI": Airfield("ETSI", "Manching", 48.0 + 42.27 / 60, 11.0 + 31.12 / 60, 64.0, 1201 * FT),   # RWY 07R [c]
    "EDDM": Airfield("EDDM", "Munich Airport", 48.0 + 20.44 / 60, 11.0 + 45.06 / 60, 81.0, 1486 * FT),  # RWY 08R [c]
    "EDDN": Airfield("EDDN", "Nuernberg", 49.0 + 30.03 / 60, 11.0 + 3.55 / 60, 96.0, 1022 * FT),  # RWY 10 [c]
}
DEFAULT_ICAO = "EDDM"  # [c] matches the reference screenshot Mission Control selection "Munich (EDDM)"


def load_airfields(override_path=None, log=print):
    """Seed table merged with per-ICAO partial overrides from a local JSON file.
    A corrupt file is reported and ignored. [a: Behaviour B11]"""
    table = copy.deepcopy(SEED)
    if not override_path or not os.path.exists(override_path):
        return table
    try:
        with open(override_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("top level is not an object")
        for icao, fields in data.items():
            if not isinstance(fields, dict):
                raise ValueError(f"{icao}: not an object")
            base = asdict(table[icao]) if icao in table else {"icao": icao, "name": icao}
            for k, v in fields.items():
                if k not in Airfield.__dataclass_fields__ or k == "icao":
                    raise ValueError(f"{icao}: unknown field {k}")
                base[k] = v
            table[icao] = Airfield(**base)
            float(table[icao].lat_deg); float(table[icao].lon_deg)
    except Exception as e:
        log(f"AIRFIELD OVERRIDE FILE IGNORED ({override_path}): {e}")
        return copy.deepcopy(SEED)
    return table
