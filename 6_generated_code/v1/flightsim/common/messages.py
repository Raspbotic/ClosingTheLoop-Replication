"""
flightsim/common/messages.py
============================
Wire protocol of the tactical federation: JSON-over-UDP dataclasses.

TRACEABILITY
------------
[a: Decision D1] "Wire format. JSON-over-UDP dataclasses: HotasControls
    (seq, roll/pitch/yaw in [-1,1], throttle in [0,1], switches dict),
    OwnshipState, RadarBeacon (beacon_id, classification FRIEND/FOE/
    UNKNOWN, lat/lon/alt, max_range_m, detected, slant_range_m, active).
    Malformed datagrams rejected with a dedicated error type."
[a: Decision D2] "Beacon protocol. Event-driven: transmit only the changed
    beacon; deletion via tombstone (active=False); background refresh every
    2.0 s; receiver garbage-collects beacons stale > 5.0 s (stale >= 2 x
    refresh)."
[a] Realizes the payload of the UdpPort datagram attribute
    (fsim_TA_Ports.sysml, port def UdpPort { attribute datagram; }).

[b] JSON was chosen by D1; the concrete key names are the dataclass field
    names, and encoding is UTF-8 - derived, not stated.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict, fields
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------
# Beacon protocol constants [a: D2]
# --------------------------------------------------------------------------

#: Background refresh period of the full beacon set, seconds. [a: D2]
BEACON_REFRESH_S = 2.0

#: Age after which a receiver garbage-collects a beacon, seconds. [a: D2]
#: D2 additionally states the invariant stale >= 2 x refresh.
BEACON_STALE_S = 5.0

#: Beacon classifications. [a: D1] and [a] L2SYS-REQ-03.02
#: ("... classify (FRIEND, FOE, UNKNOWN) radar emitters").
CLASSIFICATIONS = ("FRIEND", "FOE", "UNKNOWN")

#: Tombstone marker: deletion is signalled by active=False. [a: D2]
TOMBSTONE_ACTIVE = False


class MessageError(ValueError):
    """Dedicated error type for malformed datagrams. [a: D1]

    D1 requires malformed datagrams to be "rejected with a dedicated error
    type"; deriving from ValueError keeps it catchable by generic callers
    while remaining distinguishable [b].
    """


def _clamp(value: float, lo: float, hi: float) -> float:
    """[b] Range enforcement helper for the D1 signal ranges."""
    v = float(value)
    if not math.isfinite(v):
        raise MessageError(f"non-finite value: {value!r}")
    return lo if v < lo else (hi if v > hi else v)


class _JsonMessage:
    """Common JSON codec for the D1 dataclasses. [b]"""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> bytes:
        """formatControlPayload / message serialisation. [a: D1]"""
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes):
        """Decode a datagram, rejecting anything malformed. [a: D1]"""
        try:
            text = raw.decode("utf-8")
        except (UnicodeDecodeError, AttributeError) as exc:
            raise MessageError(f"undecodable datagram: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MessageError(f"not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise MessageError(f"datagram is not a JSON object: {type(data)}")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise MessageError(f"unknown field(s): {sorted(unknown)}")
        try:
            return cls(**data)  # type: ignore[arg-type]
        except MessageError:
            raise
        except (TypeError, ValueError) as exc:
            raise MessageError(f"malformed {cls.__name__}: {exc}") from exc


# --------------------------------------------------------------------------
# HotasControls  [a: D1]
# --------------------------------------------------------------------------

@dataclass
class HotasControls(_JsonMessage):
    """Pilot control signals, HOTAS -> PCS on port 5005.

    [a: D1] fields: seq, roll/pitch/yaw in [-1,1], throttle in [0,1],
            switches dict.
    [a] L2SYS-REQ-01.01b: "... transmit the associated signals via UDP to
            the Virtual Ownship".
    [a] HotasControl actions measureStickDeflection / measureThrottle-
            Position / measureYawTwist / readSwitches
            (fsim_TA_Peripherals.sysml).
    """

    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    throttle: float = 0.0
    switches: Dict[str, bool] = field(default_factory=dict)
    seq: int = 0

    def __post_init__(self) -> None:
        # [a: D1] range enforcement is part of the wire contract.
        self.roll = _clamp(self.roll, -1.0, 1.0)
        self.pitch = _clamp(self.pitch, -1.0, 1.0)
        self.yaw = _clamp(self.yaw, -1.0, 1.0)
        self.throttle = _clamp(self.throttle, 0.0, 1.0)
        if not isinstance(self.switches, dict):
            raise MessageError("switches must be a mapping")
        self.switches = {str(k): bool(v) for k, v in self.switches.items()}
        try:
            self.seq = int(self.seq)
        except (TypeError, ValueError) as exc:
            raise MessageError(f"bad seq: {exc}") from exc

    def button(self, index: int) -> bool:
        """[b] Convenience accessor for the btn<N> switch convention used by
        the legacy hotas_standalone.py readSwitches implementation."""
        return bool(self.switches.get(f"btn{index}", False))


# --------------------------------------------------------------------------
# OwnshipState  [a: D1]
# --------------------------------------------------------------------------

@dataclass
class OwnshipState(_JsonMessage):
    """Ownship telemetry, PCS -> orchestrator -> SEM (5006 / 5007).

    [a: D1] "OwnshipState" is a named wire dataclass.
    [a] L2SYS-REQ-01.02b names the computed physical state as
            "position, attitude, velocity" - the field groups below.
    [a] L2SYS-REQ-03.05: the SEM evaluates detection "against the received
            ownship state", hence lat/lon/alt must be present.
    [b] Field selection beyond position/attitude/velocity (agl_m, sim_time_s,
            on_ground, fuel, rpm, seq) is derived from what the PCS displays
            (01.08c) and logs (ssfLogTelemetry doc comment).
    """

    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 0.0
    agl_m: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    heading_deg: float = 0.0
    speed_mps: float = 0.0
    vspeed_mps: float = 0.0
    pitch_rate_dps: float = 0.0
    fuel_kg: float = 0.0
    rpm_pct: float = 0.0
    on_ground: bool = True
    engine_running: bool = False
    sim_time_s: float = 0.0
    seq: int = 0

    def __post_init__(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if f.type in ("float",):
                try:
                    fv = float(v)
                except (TypeError, ValueError) as exc:
                    raise MessageError(f"bad {f.name}: {exc}") from exc
                if not math.isfinite(fv):
                    raise MessageError(f"non-finite {f.name}")
                setattr(self, f.name, fv)
            elif f.type == "bool":
                setattr(self, f.name, bool(v))
            elif f.type == "int":
                try:
                    setattr(self, f.name, int(v))
                except (TypeError, ValueError) as exc:
                    raise MessageError(f"bad {f.name}: {exc}") from exc


# --------------------------------------------------------------------------
# RadarBeacon  [a: D1]
# --------------------------------------------------------------------------

@dataclass
class RadarBeacon(_JsonMessage):
    """One synthetic radar emitter, SEM -> orchestrator -> PCS (6007 / 6006).

    [a: D1] fields: beacon_id, classification FRIEND/FOE/UNKNOWN,
            lat/lon/alt, max_range_m, detected, slant_range_m, active.
    [a: D2] active=False is the deletion tombstone.
    [a: D12] max_range_m carries the radar-equation result - "published in
            the existing max_range_m field (wire unchanged)".
    [a] L2SYS-REQ-03.02 (spawn and classify with defined maximum ranges),
            L2SYS-REQ-02.03b (classification drives the ring colour).
    """

    beacon_id: int = 0
    classification: str = "UNKNOWN"
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 0.0
    max_range_m: float = 0.0
    detected: bool = False
    slant_range_m: float = 0.0
    active: bool = True

    def __post_init__(self) -> None:
        try:
            self.beacon_id = int(self.beacon_id)
        except (TypeError, ValueError) as exc:
            raise MessageError(f"bad beacon_id: {exc}") from exc
        self.classification = str(self.classification).upper()
        if self.classification not in CLASSIFICATIONS:
            # [a: D1] classification is an enumerated field; anything else
            # makes the datagram malformed.
            raise MessageError(
                f"classification must be one of {CLASSIFICATIONS}, "
                f"got {self.classification!r}")
        for name in ("lat_deg", "lon_deg", "alt_m", "max_range_m",
                     "slant_range_m"):
            try:
                v = float(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise MessageError(f"bad {name}: {exc}") from exc
            if not math.isfinite(v):
                raise MessageError(f"non-finite {name}")
            setattr(self, name, v)
        self.detected = bool(self.detected)
        self.active = bool(self.active)

    @property
    def is_tombstone(self) -> bool:
        """[a: D2] deletion via tombstone (active=False)."""
        return self.active is TOMBSTONE_ACTIVE

    def tombstone(self) -> "RadarBeacon":
        """[a: D2] Build the deletion datagram for this beacon."""
        return RadarBeacon(beacon_id=self.beacon_id,
                           classification=self.classification,
                           lat_deg=self.lat_deg, lon_deg=self.lon_deg,
                           alt_m=self.alt_m, max_range_m=self.max_range_m,
                           detected=False, slant_range_m=self.slant_range_m,
                           active=False)


# --------------------------------------------------------------------------
# Receiver-side beacon store with D2 garbage collection
# --------------------------------------------------------------------------

class BeaconStore:
    """Receiver-side beacon set implementing the D2 protocol.

    [a: D2] "deletion via tombstone (active=False) ... receiver
            garbage-collects beacons stale > 5.0 s".
    [a] L2SYS-REQ-02.03: "The PCS shall receive the threat detections
            incoming from the Synthetic Environment."
    """

    def __init__(self, stale_s: float = BEACON_STALE_S):
        if stale_s < 2.0 * BEACON_REFRESH_S:
            # [a: D2] "stale >= 2 x refresh" - enforced, not assumed.
            raise MessageError("stale window must be >= 2 x refresh period")
        self._stale_s = float(stale_s)
        self._beacons: Dict[int, RadarBeacon] = {}
        self._seen: Dict[int, float] = {}

    def apply(self, beacon: RadarBeacon, now: float) -> None:
        """Ingest one received beacon datagram."""
        if beacon.is_tombstone:                       # [a: D2]
            self._beacons.pop(beacon.beacon_id, None)
            self._seen.pop(beacon.beacon_id, None)
            return
        self._beacons[beacon.beacon_id] = beacon
        self._seen[beacon.beacon_id] = float(now)

    def collect(self, now: float) -> None:
        """Garbage-collect beacons not refreshed within the stale window.
        [a: D2]"""
        dead = [bid for bid, t in self._seen.items()
                if float(now) - t > self._stale_s]
        for bid in dead:
            self._beacons.pop(bid, None)
            self._seen.pop(bid, None)

    def get(self, beacon_id: int) -> Optional[RadarBeacon]:
        return self._beacons.get(int(beacon_id))

    def values(self):
        return list(self._beacons.values())

    def __len__(self) -> int:
        return len(self._beacons)

    def __contains__(self, beacon_id: object) -> bool:
        return int(beacon_id) in self._beacons  # type: ignore[arg-type]


def decode_any(raw: bytes):
    """[b] Best-effort dispatch used by diagnostic tooling only; the
    production paths decode with the concrete class of their port."""
    for cls in (RadarBeacon, HotasControls, OwnshipState):
        try:
            return cls.from_json(raw)
        except MessageError:
            continue
    raise MessageError("datagram matches no known message type")


__all__ = [
    "BEACON_REFRESH_S", "BEACON_STALE_S", "CLASSIFICATIONS",
    "TOMBSTONE_ACTIVE", "MessageError", "HotasControls", "OwnshipState",
    "RadarBeacon", "BeaconStore", "decode_any",
]
