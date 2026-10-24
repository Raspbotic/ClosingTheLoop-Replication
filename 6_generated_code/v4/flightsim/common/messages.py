"""Federation messages: JSON datagrams over UDP, one message per datagram.

[a: Behaviour B1] Field sets follow B1. The model's UdpPort `datagram` attribute is untyped
(fsim_TA_Ports.sysml), so field names are [c] (finding F2), ownship fields [b] from the
ssfLogTelemetry field list (fsim_TA_PCS_ThreadFlightSim_UIM.sysml).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict

# [a: Behaviour B2] refresh 2.0 s; receiver removes a beacon after two refresh periods.
BEACON_REFRESH_S = 2.0
BEACON_STALE_S = 2.0 * BEACON_REFRESH_S

CLASSIFICATIONS = ("FRIEND", "FOE", "UNKNOWN")  # [a: L2SYS-REQ-03.02]


class MessageError(ValueError):
    """Rejected datagram. [a: Behaviour B1] error type of this application."""


def _num(d, key, lo=None, hi=None):
    if key not in d:
        raise MessageError(f"missing field {key}")
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise MessageError(f"field {key} not a finite number")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise MessageError(f"field {key}={v} outside [{lo},{hi}]")
    return float(v)


def _int(d, key, lo=0):
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise MessageError(f"field {key} not an integer >= {lo}")
    return v


def _bool(d, key):
    v = d.get(key)
    if not isinstance(v, bool):
        raise MessageError(f"field {key} not a boolean")
    return v


@dataclass
class HotasControls:
    """Peripheral controls. [a: Behaviour B1] sequence number + normalized axes.
    Signs: roll +right, pitch +stick back [a: Behaviour B8], yaw +right pedal [c],
    throttle 0 idle .. 1 full [b: legacy hotas_standalone.py]."""
    seq: int
    roll: float
    pitch: float
    yaw: float
    throttle: float
    gear_toggles: int = 0      # [c] edge counters survive datagram loss (B5 toggles)
    airbrake_toggles: int = 0  # [c]
    wheel_brake: bool = False  # [a: Behaviour B5] momentary
    TYPE = "hotas"

    def validate(self):
        d = asdict(self)
        _int(d, "seq")
        for k in ("roll", "pitch", "yaw"):
            _num(d, k, -1.0, 1.0)
        _num(d, "throttle", 0.0, 1.0)
        _int(d, "gear_toggles")
        _int(d, "airbrake_toggles")
        _bool(d, "wheel_brake")
        return self


@dataclass
class OwnshipState:
    """Ownship state. [b: ssfLogTelemetry field list] units per field name."""
    seq: int
    sim_time_s: float
    lat_deg: float
    lon_deg: float
    alt_m: float
    agl_m: float
    v_speed_mps: float
    speed_mps: float
    heading_deg: float
    pitch_deg: float
    roll_deg: float
    TYPE = "ownship"

    def validate(self):
        d = asdict(self)
        _int(d, "seq")
        _num(d, "sim_time_s", 0.0)
        _num(d, "lat_deg", -90.0, 90.0)
        _num(d, "lon_deg", -180.0, 180.0)
        _num(d, "alt_m", -500.0, 30000.0)
        _num(d, "agl_m", -100.0, 30000.0)
        _num(d, "v_speed_mps", -1000.0, 1000.0)
        _num(d, "speed_mps", 0.0, 1500.0)
        _num(d, "heading_deg", 0.0, 360.0)
        _num(d, "pitch_deg", -90.0, 90.0)
        _num(d, "roll_deg", -180.0, 180.0)
        return self


@dataclass
class RadarBeacon:
    """Radar beacon. [a: Behaviour B1] id, classification, position, max range,
    detection flag, slant range, active flag. [a: Behaviour B10] max_range_m carries R_max."""
    beacon_id: int
    classification: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    max_range_m: float
    detected: bool = False
    slant_range_m: float = -1.0  # [c] -1 = no ownship state received yet
    active: bool = True          # [a: Behaviour B2] tombstone = False
    TYPE = "beacon"

    def validate(self):
        d = asdict(self)
        _int(d, "beacon_id")
        if d["classification"] not in CLASSIFICATIONS:
            raise MessageError(f"classification {d['classification']!r} invalid")
        _num(d, "lat_deg", -90.0, 90.0)
        _num(d, "lon_deg", -180.0, 180.0)
        _num(d, "alt_m", -500.0, 30000.0)
        _num(d, "max_range_m", 0.0, 1.0e7)
        _bool(d, "detected")
        _num(d, "slant_range_m", -1.0)
        _bool(d, "active")
        return self


_TYPES = {c.TYPE: c for c in (HotasControls, OwnshipState, RadarBeacon)}


def encode(msg) -> bytes:
    msg.validate()
    d = asdict(msg)
    d["type"] = msg.TYPE
    return json.dumps(d, separators=(",", ":")).encode("utf-8")


def decode(data: bytes):
    """Decode and validate one datagram; raise MessageError on any defect."""
    try:
        d = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise MessageError(f"undecodable datagram: {e}") from None
    if not isinstance(d, dict):
        raise MessageError("datagram is not a JSON object")
    cls = _TYPES.get(d.pop("type", None))
    if cls is None:
        raise MessageError("unknown or missing message type")
    names = set(cls.__dataclass_fields__)
    extra = set(d) - names
    if extra:
        raise MessageError(f"unexpected fields {sorted(extra)}")
    try:
        msg = cls(**d)
    except TypeError as e:
        raise MessageError(f"missing fields: {e}") from None
    return msg.validate()
