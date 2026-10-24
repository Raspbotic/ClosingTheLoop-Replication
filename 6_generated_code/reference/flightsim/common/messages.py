"""
flightsim.common.messages
=========================
JSON message schemas for all sim-internal UDP links.

TRACEABILITY
------------
[a]  The EXISTENCE and DIRECTION of each message follows the TA port model
     and the requirements spreadsheet (fsim_Design.xlsx):
       - HotasControls : L2SYS-REQ-01.01 (Hotas polls axes/buttons, tx UDP)
       - OwnshipState  : L2SYS-REQ-01.02 (VirtualOwnship computes position,
                         attitude, velocity at 120 Hz)
       - RadarBeacon   : L2SYS-REQ-03.05 (detection status; computed
                         SEM-side via the monostatic radar equation,
                         rev. 2026-07-11 -- max_range_m carries the
                         computed effective detection range),
                         L2SYS-REQ-03.02 (FRIEND/FOE/UNKNOWN classification,
                         defined maximum range)

[a]  BEACON TRANSMISSION PROTOCOL (customer decision, 2026-07-11, revising
     the earlier SEM-team TDM baseline): EVENT-DRIVEN, one beacon per
     datagram. Whenever beacon[i] changes (created, moved, reclassified,
     detection status changed, DELETED), the SEM transmits only beacon[i]
     immediately. Deletion is signalled explicitly by 'active = false'
     (tombstone); the PCS removes that beacon_id on receipt.
     Datagram size stays bounded independently of beacon count (the
     original TDM motivation is preserved).

[c]  LOSS-REPAIR LAYER (developer recommendation, since UDP offers no
     delivery guarantee across the SEM -> orchestrator -> PCS path):
       - SEM re-transmits each LIVE beacon every BEACON_REFRESH_PERIOD_S
         as a slow background TDM cycle (heals lost create/update
         datagrams; set to 0 to disable for pure event-driven).
       - PCS ages a beacon out after BEACON_STALE_TIMEOUT_S without any
         update -- last-resort GC only, covering a lost tombstone or a
         dead SEM. With the refresh layer on, live beacons can never
         legitimately go stale.
     Consequence [b]: the PCS maintains a beacon table keyed by
     beacon_id; removal latency is one network hop (tombstone), no
     longer the staleness timeout.

[c]  The FIELD-LEVEL schema (names, units) is a developer proposal: the TA
     types the payload only as an untyped 'datagram' attribute. JSON as
     wire format was confirmed by the customer (open point 2, 2026-07-11).

CONVENTIONS [c]
---------------
- Angles in degrees, altitudes in meters, speeds in m/s, ranges in meters.
- Geographic positions as WGS84 decimal degrees.
- Every message carries 'msg_type' for on-the-wire discrimination and a
  monotonically increasing 'seq' for loss detection.

NOTE: the physics -> FlightGear link (UDP 5550) does NOT use these classes.
It uses FlightGear's binary native-fdm structure [a: TA port name
'e_nativeJSBSimFdmOut', FlightGear documented protocol]. That encoder
lives in pcs/physics.py (Step 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any, Dict


# [c] Background refresh period for LIVE beacons (loss repair; 0 disables).
BEACON_REFRESH_PERIOD_S = 2.0

# [c] Last-resort GC: PCS drops a beacon after this silence. Must be a
# comfortable multiple of BEACON_REFRESH_PERIOD_S (here 2.5x) so a single
# lost refresh never evicts a live beacon.
BEACON_STALE_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Base serialization helpers
# ---------------------------------------------------------------------------

class MessageError(ValueError):
    """Raised when an incoming datagram cannot be parsed into a message."""


@dataclass
class _JsonMessage:
    """Common encode/decode behaviour for all JSON UDP messages."""

    def to_json(self) -> bytes:
        d = asdict(self)
        d["msg_type"] = self.MSG_TYPE  # type: ignore[attr-defined]
        return json.dumps(d, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "_JsonMessage":
        try:
            d: Dict[str, Any] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MessageError(f"malformed datagram: {exc}") from exc
        if d.pop("msg_type", None) != cls.MSG_TYPE:  # type: ignore[attr-defined]
            raise MessageError(f"unexpected msg_type for {cls.__name__}")
        try:
            return cls(**d)
        except TypeError as exc:
            raise MessageError(f"schema mismatch for {cls.__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# HOTAS -> PCS  (UDP 5005)
# [a] L2SYS-REQ-01.01: axes and buttons
# [b] field selection: TA actions measureStickDeflection, measureThrottle-
#     Position, measureYawTwist, readSwitches map 1:1 onto the fields below
# ---------------------------------------------------------------------------

@dataclass
class HotasControls(_JsonMessage):
    MSG_TYPE = "hotas_controls"

    seq: int = 0
    roll: float = 0.0        # stick deflection X, normalized [-1.0 .. +1.0]
    pitch: float = 0.0       # stick deflection Y, normalized [-1.0 .. +1.0]
    yaw: float = 0.0         # yaw twist,          normalized [-1.0 .. +1.0]
    throttle: float = 0.0    # throttle position,  normalized [ 0.0 .. +1.0]
    switches: Dict[str, bool] = field(default_factory=dict)  # named buttons


# ---------------------------------------------------------------------------
# PCS -> Orchestrator -> SEM  (UDP 5006 / 6007)
# [a] L2SYS-REQ-01.02: position, attitude, velocity
# ---------------------------------------------------------------------------

@dataclass
class OwnshipState(_JsonMessage):
    MSG_TYPE = "ownship_state"

    seq: int = 0
    sim_time_s: float = 0.0
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 0.0            # altitude MSL
    heading_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    groundspeed_ms: float = 0.0
    vertical_speed_ms: float = 0.0


# ---------------------------------------------------------------------------
# SEM -> Orchestrator -> PCS  (UDP 5007 / 6006), ONE beacon per datagram,
# EVENT-DRIVEN with background refresh (see module docstring)
# [a] L2SYS-REQ-03.02: classification FRIEND/FOE/UNKNOWN, max range
# [a] L2SYS-REQ-03.05: detection status via slant range
# [a] L2SYS-REQ-02.03: PCS renders scaled, color-coded rings -> needs
#     position + max range + classification per beacon
# [a] 'active' tombstone per customer decision 2026-07-11: an explicit
#     deletion indicator was requested; the boolean-field form (rather
#     than magic default values) is the developer's choice [c].
# ---------------------------------------------------------------------------

class BeaconClass(str, Enum):
    FRIEND = "FRIEND"
    FOE = "FOE"
    UNKNOWN = "UNKNOWN"


@dataclass
class RadarBeacon(_JsonMessage):
    """One radar emitter update. Sent on any state change of this beacon,
    and periodically re-sent while live (loss repair).

    active = True  : create-or-update this beacon in the PCS table.
    active = False : TOMBSTONE -- the instructor deleted this beacon;
                     the PCS removes beacon_id on receipt. All other
                     fields may carry the last known state but the PCS
                     must not render them.
    """
    MSG_TYPE = "radar_beacon"

    seq: int = 0                  # SEM-global tx sequence number
    sim_time_s: float = 0.0
    beacon_id: str = ""
    active: bool = True           # False = deletion tombstone
    classification: str = BeaconClass.UNKNOWN.value
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 0.0
    max_range_m: float = 0.0      # detection envelope radius
    detected: bool = False        # ownship currently inside envelope?
    slant_range_m: float = -1.0   # current slant range to ownship (-1 = n/a)


# ---------------------------------------------------------------------------
# Generic decoder for receive loops that may see several message types
# ---------------------------------------------------------------------------

_REGISTRY = {
    cls.MSG_TYPE: cls
    for cls in (HotasControls, OwnshipState, RadarBeacon)
}


def decode(raw: bytes) -> _JsonMessage:
    """Decode any known JSON datagram; raises MessageError otherwise."""
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MessageError(f"malformed datagram: {exc}") from exc
    cls = _REGISTRY.get(d.get("msg_type"))
    if cls is None:
        raise MessageError(f"unknown msg_type: {d.get('msg_type')!r}")
    return cls.from_json(raw)