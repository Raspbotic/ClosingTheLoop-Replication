"""The wire format: JSON-over-UDP dataclasses, their codec, and the D2 constants.

[a: D1] Wire format. JSON-over-UDP dataclasses: HotasControls (seq; roll, pitch,
yaw in [-1, 1]; throttle in [0, 1]; switches dict), OwnshipState (fields per
D19), RadarBeacon (beacon_id, classification FRIEND/FOE/UNKNOWN, lat, lon, alt,
max_range_m, detected, slant_range_m, active). A malformed datagram is rejected
with a dedicated error type of the application's own. Ranges are enforced on
decode as well as on construction.

[a: D19] OwnshipState fields and units.
[a: D2] Beacon protocol constants.

Standard library only, so this module imports identically under .venv_fsim
(3.14) and .venv_hotas (3.11). [b: D5]
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# D2 constants
# ---------------------------------------------------------------------------

#: [a: D2] "Background refresh every 2.0 s."
BEACON_REFRESH_S = 2.0

#: [a: D2] "The receiver discards beacons stale for more than 5.0 s
#: (stale >= 2 x refresh)." The relation is asserted, not merely commented, so a
#: future edit to either number cannot silently break it.
BEACON_STALE_S = 5.0

assert BEACON_STALE_S >= 2.0 * BEACON_REFRESH_S, (
    "D2 requires stale >= 2 x refresh"
)

#: [a: D1] The three permitted classifications.
CLASSIFICATIONS = ("FRIEND", "FOE", "UNKNOWN")

#: [a: D10] Physical-precedence window.
PHYSICAL_PRECEDENCE_S = 1.0


# ---------------------------------------------------------------------------
# The application's own error type
# ---------------------------------------------------------------------------
class WireFormatError(Exception):
    """Raised for any datagram this module refuses. [a: D1]

    D1 requires "a dedicated error type of the application's own". Deliberately
    derived from `Exception` and **not** from ValueError, KeyError, TypeError or
    json.JSONDecodeError, so that a caller catching this type cannot
    accidentally catch an unrelated library failure, and so a test can assert
    that the refusal is this project's and not the standard library's.
    """


def _num(value: Any, name: str) -> float:
    """Coerce to float, refusing bool and non-numerics. [b: D1]

    bool is a subclass of int in Python, so `True` would silently become 1.0.
    A bool where a number belongs is a malformed datagram. [c] -- what would
    settle a laxer rule: a stated tolerance for type coercion on the wire,
    which D1 does not give.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WireFormatError(f"{name}: expected a number, got {type(value).__name__}")
    v = float(value)
    if not math.isfinite(v):
        raise WireFormatError(f"{name}: expected a finite number, got {value!r}")
    return v


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise WireFormatError(f"{name}: expected a bool, got {type(value).__name__}")
    return value


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise WireFormatError(f"{name}: expected a string, got {type(value).__name__}")
    return value


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WireFormatError(f"{name}: expected an int, got {type(value).__name__}")
    return value


def _in_range(v: float, lo: float, hi: float, name: str) -> float:
    """[a: D1] Ranges are enforced -- refused, not clipped."""
    if not (lo <= v <= hi):
        raise WireFormatError(f"{name}: {v!r} is outside [{lo}, {hi}]")
    return v


# ---------------------------------------------------------------------------
# HotasControls  [a: D1]
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HotasControls:
    """Control state from the HOTAS transmitter. [a: D1] [a: L2SYS-REQ-01.01b]

    seq        monotonic sequence number, so a receiver can detect loss/reorder
    roll       [-1, 1]   [a: D1]
    pitch      [-1, 1]   [a: D1]; drag-down / stick back is +1 [a: D10]
    yaw        [-1, 1]   [a: D1]
    throttle   [0, 1]    [a: D1]
    switches   raw pressed states, keyed by button index as a string.
               [a: D7] "The transmitter puts raw pressed states on the wire;
               toggle edges for btn2 and btn4 are detected by the receiver."
    """

    seq: int
    roll: float
    pitch: float
    yaw: float
    throttle: float
    switches: dict = field(default_factory=dict)

    KIND = "hotas"

    def __post_init__(self) -> None:
        # Ranges enforced on construction. [a: D1]
        object.__setattr__(self, "seq", _int(self.seq, "seq"))
        for name, lo, hi in (
            ("roll", -1.0, 1.0),
            ("pitch", -1.0, 1.0),
            ("yaw", -1.0, 1.0),
            ("throttle", 0.0, 1.0),
        ):
            v = _in_range(_num(getattr(self, name), name), lo, hi, name)
            object.__setattr__(self, name, v)
        if not isinstance(self.switches, dict):
            raise WireFormatError("switches: expected a dict")
        for k, v in self.switches.items():
            if not isinstance(k, str):
                raise WireFormatError("switches: keys must be strings on the wire")
            _bool(v, f"switches[{k}]")


# ---------------------------------------------------------------------------
# OwnshipState  [a: D1, D19]
# ---------------------------------------------------------------------------

#: [a: D19] The field list, in the order D19 gives it. Transcribed literally so
#: closure-audit check 1 and the C-D1-7 plan row can compare against D19 itself.
OWNSHIP_FIELDS = (
    "seq",
    "sim_time_s",
    "lat_deg",
    "lon_deg",
    "alt_m",
    "agl_m",
    "heading_deg",
    "pitch_deg",
    "roll_deg",
    "pitch_rate_dps",
    "speed_mps",
    "vspeed_mps",
    "rpm_pct",
    "fuel_lbs",
    "engine_running",
    "parking_brake",
    "air_brake",
    "gear_down",
    "on_ground",
    "aileron_cmd",
    "elevator_cmd",
    "rudder_cmd",
    "throttle_cmd",
    "trim_pitch",
    "trim_roll",
    "trim_yaw",
    "model",
)

_OWNSHIP_BOOLS = frozenset(
    {"engine_running", "parking_brake", "air_brake", "gear_down", "on_ground"}
)


@dataclass(frozen=True)
class OwnshipState:
    """Ownship telemetry. [a: D1] [a: D19]

    "Angles in degrees, distances in metres, speeds in m/s, fuel in pounds,
    rpm_pct a percentage." [a: D19]
    `speed_mps` is true airspeed. [a: D20, provisional]
    `engine_running` is the state commanded by the ENGINE toggle, not a
    measured engine speed. [a: D21]
    """

    seq: int
    sim_time_s: float
    lat_deg: float
    lon_deg: float
    alt_m: float
    agl_m: float
    heading_deg: float
    pitch_deg: float
    roll_deg: float
    pitch_rate_dps: float
    speed_mps: float
    vspeed_mps: float
    rpm_pct: float
    fuel_lbs: float
    engine_running: bool
    parking_brake: bool
    air_brake: bool
    gear_down: bool
    on_ground: bool
    aileron_cmd: float
    elevator_cmd: float
    rudder_cmd: float
    throttle_cmd: float
    trim_pitch: float
    trim_roll: float
    trim_yaw: float
    model: str

    KIND = "ownship"

    def __post_init__(self) -> None:
        object.__setattr__(self, "seq", _int(self.seq, "seq"))
        object.__setattr__(self, "model", _str(self.model, "model"))
        for name in _OWNSHIP_BOOLS:
            object.__setattr__(self, name, _bool(getattr(self, name), name))
        for name in OWNSHIP_FIELDS:
            if name in _OWNSHIP_BOOLS or name in ("seq", "model"):
                continue
            object.__setattr__(self, name, _num(getattr(self, name), name))


# The dataclass field order must be D19's field order, or the C-D1-7 row is
# vacuous. Asserted at import so a reordering edit fails immediately. [b: D19]
assert tuple(f.name for f in fields(OwnshipState)) == OWNSHIP_FIELDS, (
    "OwnshipState field order must match D19"
)


# ---------------------------------------------------------------------------
# RadarBeacon  [a: D1]
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RadarBeacon:
    """One radar emitter as published by the SEM. [a: D1] [a: D2]

    max_range_m    the effective maximum detection range, published by the SEM
                   [a: D12] "published in max_range_m"
    detected       slant range <= R_max, inclusive [a: D12]
    active         False is a deletion tombstone [a: D2]
    """

    beacon_id: str
    classification: str
    lat: float
    lon: float
    alt: float
    max_range_m: float
    detected: bool
    slant_range_m: float
    active: bool = True

    KIND = "beacon"

    def __post_init__(self) -> None:
        object.__setattr__(self, "beacon_id", _str(self.beacon_id, "beacon_id"))
        c = _str(self.classification, "classification")
        if c not in CLASSIFICATIONS:
            raise WireFormatError(
                f"classification: {c!r} is not one of {CLASSIFICATIONS}"
            )
        for name in ("lat", "lon", "alt", "max_range_m", "slant_range_m"):
            object.__setattr__(self, name, _num(getattr(self, name), name))
        # Latitude and longitude have physical ranges; a beacon outside them is
        # a malformed datagram, not a distant beacon. [b: D1]
        _in_range(self.lat, -90.0, 90.0, "lat")
        _in_range(self.lon, -180.0, 180.0, "lon")
        if self.max_range_m < 0.0:
            raise WireFormatError(f"max_range_m: {self.max_range_m!r} is negative")
        if self.slant_range_m < 0.0:
            raise WireFormatError(f"slant_range_m: {self.slant_range_m!r} is negative")
        for name in ("detected", "active"):
            object.__setattr__(self, name, _bool(getattr(self, name), name))


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------

_KIND_KEY = "kind"

_BY_KIND: dict[str, type] = {
    HotasControls.KIND: HotasControls,
    OwnshipState.KIND: OwnshipState,
    RadarBeacon.KIND: RadarBeacon,
}


def encode(message: Any) -> bytes:
    """Serialize one message to a UDP payload. [a: D1]

    The `kind` discriminator is [b]: D1 names three dataclasses on one
    federation but does not say how a receiver tells them apart. Every link in
    D4 carries exactly one type, so a receiver could infer it from the socket;
    tagging makes the orchestrator's opacity (D3) and the round-trip rows
    checkable without reference to which socket a payload arrived on.
    """
    if type(message) not in _BY_KIND.values():
        raise WireFormatError(f"cannot encode {type(message).__name__}")
    payload = asdict(message)
    payload[_KIND_KEY] = message.KIND
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode(data: bytes) -> Any:
    """Parse a UDP payload into its message. [a: D1]

    Every failure path raises WireFormatError and nothing else -- a truncated
    datagram, non-JSON bytes, JSON that is not an object, an unknown kind, a
    missing field, an extra field, a wrong-typed field, and a value outside its
    D1 range. Ranges are enforced here as well as on construction, which is what
    D1 requires: a datagram is not trusted because it decoded.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise WireFormatError(f"expected bytes, got {type(data).__name__}")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WireFormatError(f"payload is not UTF-8: {exc}") from exc
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WireFormatError(f"payload is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WireFormatError(
            f"payload is JSON but not an object: {type(payload).__name__}"
        )

    kind = payload.pop(_KIND_KEY, None)
    if kind is None:
        raise WireFormatError("payload carries no message kind")
    if not isinstance(kind, str) or kind not in _BY_KIND:
        raise WireFormatError(f"unknown message kind {kind!r}")

    cls = _BY_KIND[kind]
    expected = {f.name for f in fields(cls)}
    missing = expected - payload.keys()
    if missing:
        raise WireFormatError(f"{kind}: missing field(s) {sorted(missing)}")
    extra = payload.keys() - expected
    if extra:
        raise WireFormatError(f"{kind}: unexpected field(s) {sorted(extra)}")

    # The dataclass __post_init__ re-validates every type and every range, so
    # decode is not a second, weaker gate. [a: D1]
    return cls(**payload)


def try_decode(data: bytes) -> Any | None:
    """`decode` that returns None instead of raising.

    For a receive loop that must not die on one bad datagram. The refusal is
    still this module's decision; the caller simply chose not to be told about
    it. [b: D1]
    """
    try:
        return decode(data)
    except WireFormatError:
        return None


# ---------------------------------------------------------------------------
# Sequence numbering
# ---------------------------------------------------------------------------
class SeqCounter:
    """Monotonic sequence source for a transmitter. [b: D1]

    D1 gives HotasControls a `seq` but does not say where it comes from. A
    per-transmitter counter starting at 0 and never reused within a run is the
    minimum that makes `seq` useful for loss and reorder detection. [c] -- what
    would settle wrap-around behaviour: a stated field width, which D1 omits;
    Python ints do not wrap, so none is imposed.
    """

    __slots__ = ("_n",)

    def __init__(self, start: int = 0) -> None:
        self._n = int(start)

    def next(self) -> int:
        n = self._n
        self._n += 1
        return n

    @property
    def value(self) -> int:
        return self._n


def is_stale(last_seen_s: float, now_s: float) -> bool:
    """[a: D2] "discards beacons stale for more than 5.0 s"."""
    return (now_s - last_seen_s) > BEACON_STALE_S


def as_mapping(message: Any) -> Mapping[str, Any]:
    """Field mapping of a message, without the wire discriminator. [b]"""
    return asdict(message)
