"""Synthetic Environment model layer -- Qt-free.

[a: R-7] "Keep the model layer of both free of Qt." Everything the Synthetic
Environment computes lives here; `flightsim/run_sem.py` is the widget over it.

Realizes the `threadMainGui` actions of `fsim_TA_SEM.sysml`:
    manageEwScenario / manageEwBeacon -> Scenario.add / edit / remove
    controlOwnshipRcs                 -> Scenario.set_ownship_rcs
    calcBeaconOwnshipRange            -> slant_range_m
    calcRadarEquation                 -> max_detection_range_m   [a: 03.05]
    connectToSim                      -> ScenarioPublisher

[a: L2SYS-REQ-03.01] a Synthetic Environment for external entities.
[a: L2SYS-REQ-03.02] spawn and classify FRIEND / FOE / UNKNOWN with defined
    maximum ranges.
[a: L2SYS-REQ-03.03] per-beacon emitter parameters of the radar equation.
[a: L2SYS-REQ-03.04] ownship radar cross section as a scenario parameter.
[a: L2SYS-REQ-03.05] compute each beacon's detection status by evaluating the
    monostatic radar equation against the received ownship state.
"""

from __future__ import annotations

import math
import socket
import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

from flightsim.common import messages, ports
from flightsim.common.messages import (
    BEACON_REFRESH_S,
    CLASSIFICATIONS,
    OwnshipState,
    RadarBeacon,
    encode,
)

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

#: Speed of light, m/s. Exact by the SI definition of the metre. [c] -- a
#: physical constant, not something a supplied artifact states; D12 writes
#: "lambda = c / f" and leaves c to physics.
SPEED_OF_LIGHT_MPS = 299_792_458.0

#: Mean Earth radius, m (IUGG). Used for the great-circle ground distance in
#: `slant_range_m`. [c] -- what would settle a different figure: a stated
#: geodetic model; neither the model nor the Decisions name one, and at the
#: ranges in play (tens of km) the spherical figure is within a few metres of
#: WGS-84.
EARTH_RADIUS_M = 6_371_000.0

#: [a: D12] the (4*pi)^3 of the monostatic radar equation.
FOUR_PI_CUBED = (4.0 * math.pi) ** 3


# ---------------------------------------------------------------------------
# D12 defaults
# ---------------------------------------------------------------------------

#: [a: D12] "defaults 100 kW, 35 dBi, 10 GHz, -90 dBm"
DEFAULT_PT_W = 100_000.0            # 100 kW
DEFAULT_GAIN_DBI = 35.0             # 35 dBi
DEFAULT_FREQ_HZ = 10e9              # 10 GHz
DEFAULT_PMIN_DBM = -90.0            # -90 dBm
#: [a: D12] "and L (default 1.0)"
DEFAULT_LOSS = 1.0
#: [a: D12] "Ownship RCS is a scenario spinbox, default 5 m^2"
DEFAULT_OWNSHIP_RCS_M2 = 5.0


def dbi_to_linear(gain_dbi: float) -> float:
    """Antenna gain from dBi to a linear ratio. [b: D12]

    G_linear = 10 ** (G_dBi / 10). D12 gives the default as "35 dBi", so the
    equation's G is the linear form of that figure.
    """
    return 10.0 ** (gain_dbi / 10.0)


def dbm_to_watts(power_dbm: float) -> float:
    """Power from dBm to watts. [b: D12]

    0 dBm is 1 mW, so P_W = 1e-3 * 10 ** (dBm / 10).
    -90 dBm = 1e-3 * 10 ** -9 = 1e-12 W.
    """
    return 1e-3 * (10.0 ** (power_dbm / 10.0))


def wavelength_m(frequency_hz: float) -> float:
    """[a: D12] "lambda = c / f".

    D12 makes frequency the editable parameter and wavelength a derived
    display. Reported in docs/MODEL_INTAKE.md F-5: the reference screenshot
    exposes wavelength as the editable field and has no frequency field. A
    Decision outranks the screenshot on data (Appendix A rule 2).
    """
    if frequency_hz <= 0.0:
        raise ValueError("frequency must be positive")
    return SPEED_OF_LIGHT_MPS / frequency_hz


# ---------------------------------------------------------------------------
# The radar equation [a: D12] [a: L2SYS-REQ-03.05]
# ---------------------------------------------------------------------------
def max_detection_range_m(
    pt_w: float = DEFAULT_PT_W,
    gain_dbi: float = DEFAULT_GAIN_DBI,
    frequency_hz: float = DEFAULT_FREQ_HZ,
    pmin_dbm: float = DEFAULT_PMIN_DBM,
    rcs_m2: float = DEFAULT_OWNSHIP_RCS_M2,
    loss: float = DEFAULT_LOSS,
) -> float:
    """The monostatic radar equation. [a: D12] [a: L2SYS-REQ-03.05]

        R_max = (Pt * G^2 * lambda^2 * sigma / ((4*pi)^3 * P_min * L)) ^ (1/4)
        lambda = c / f

    At the D12 defaults (100 kW, 35 dBi, 10 GHz, -90 dBm, sigma 5 m^2, L 1.0)
    this gives about 38.8 km, which is the "about 39 km" D12 states.

    Published in `max_range_m`. [a: D12]
    """
    g = dbi_to_linear(gain_dbi)
    lam = wavelength_m(frequency_hz)
    p_min = dbm_to_watts(pmin_dbm)
    if p_min <= 0.0 or loss <= 0.0:
        raise ValueError("P_min and L must be positive")
    if pt_w < 0.0 or rcs_m2 < 0.0:
        raise ValueError("Pt and RCS must be non-negative")
    numerator = pt_w * (g ** 2) * (lam ** 2) * rcs_m2
    denominator = FOUR_PI_CUBED * p_min * loss
    return (numerator / denominator) ** 0.25


def slant_range_m(
    own_lat_deg: float, own_lon_deg: float, own_alt_m: float,
    beacon_lat_deg: float, beacon_lon_deg: float, beacon_alt_m: float,
) -> float:
    """calcBeaconOwnshipRange. [a: fsim_TA_SEM.sysml] [a: D1]

    Great-circle ground distance on a sphere of `EARTH_RADIUS_M`, combined with
    the altitude difference by Pythagoras. Published in `slant_range_m`.
    """
    lat1 = math.radians(own_lat_deg)
    lat2 = math.radians(beacon_lat_deg)
    dlat = lat2 - lat1
    dlon = math.radians(beacon_lon_deg - own_lon_deg)
    a = (math.sin(dlat / 2.0) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2)
    ground = 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))
    dalt = beacon_alt_m - own_alt_m
    return math.hypot(ground, dalt)


def is_detected(slant_m: float, max_range_m: float) -> bool:
    """[a: D12] "Detection = slant range <= R_max, inclusive.\""""
    return slant_m <= max_range_m


# ---------------------------------------------------------------------------
# Beacons
# ---------------------------------------------------------------------------
@dataclass
class EmitterParameters:
    """Per-beacon radar-equation parameters. [a: L2SYS-REQ-03.03] [a: D12]

    "Per-beacon editable Pt, G, f, P_min (defaults 100 kW, 35 dBi, 10 GHz,
    -90 dBm) and L (default 1.0). Frequency is the editable parameter;
    wavelength is displayed derived."
    """

    pt_w: float = DEFAULT_PT_W
    gain_dbi: float = DEFAULT_GAIN_DBI
    frequency_hz: float = DEFAULT_FREQ_HZ
    pmin_dbm: float = DEFAULT_PMIN_DBM
    loss: float = DEFAULT_LOSS

    @property
    def wavelength_m(self) -> float:
        """Displayed derived, never edited. [a: D12]"""
        return wavelength_m(self.frequency_hz)


@dataclass
class Beacon:
    """One emitter in the scenario. [a: L2SYS-REQ-03.02]

    `emitter` None means the beacon keeps its **explicit** range:
    [a: D12] "Beacons without emitter parameters keep their explicit range and
    are not recomputed on an RCS change."
    """

    beacon_id: str
    classification: str
    lat: float
    lon: float
    alt: float = 0.0
    explicit_range_m: float = 0.0
    emitter: EmitterParameters | None = field(default=None)

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(
                f"classification must be one of {CLASSIFICATIONS}, "
                f"got {self.classification!r}"
            )

    @property
    def parameterized(self) -> bool:
        return self.emitter is not None

    def max_range_m(self, ownship_rcs_m2: float) -> float:
        """[a: D12] published in `max_range_m`."""
        if self.emitter is None:
            return self.explicit_range_m
        return max_detection_range_m(
            pt_w=self.emitter.pt_w,
            gain_dbi=self.emitter.gain_dbi,
            frequency_hz=self.emitter.frequency_hz,
            pmin_dbm=self.emitter.pmin_dbm,
            rcs_m2=ownship_rcs_m2,
            loss=self.emitter.loss,
        )


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------
class Scenario:
    """The beacon roster and the ownship RCS. Qt-free. [a: R-7]

    Publication is **event-driven** [a: D2]: "only the changed beacon is
    transmitted". Every mutating method returns the list of beacons that must
    go on the wire as a result, and the publisher sends exactly those.
    """

    def __init__(
        self,
        ownship_rcs_m2: float = DEFAULT_OWNSHIP_RCS_M2,
        publish: Callable[[RadarBeacon], None] | None = None,
    ) -> None:
        self._beacons: dict[str, Beacon] = {}
        self._order: list[str] = []
        self._rcs = float(ownship_rcs_m2)
        self._ownship: OwnshipState | None = None
        self._publish = publish
        self.published: list[RadarBeacon] = []

    # -- roster --------------------------------------------------------
    @property
    def beacons(self) -> tuple[Beacon, ...]:
        return tuple(self._beacons[i] for i in self._order)

    @property
    def ownship_rcs_m2(self) -> float:
        return self._rcs

    @property
    def ownship(self) -> OwnshipState | None:
        return self._ownship

    def get(self, beacon_id: str) -> Beacon | None:
        return self._beacons.get(beacon_id)

    # -- manageEwBeacon [a: fsim_TA_SEM.sysml] -------------------------
    def add(self, beacon: Beacon) -> RadarBeacon:
        """[a: L2SYS-REQ-03.02] spawn and classify an emitter."""
        if beacon.beacon_id in self._beacons:
            raise ValueError(f"beacon {beacon.beacon_id!r} already exists")
        self._beacons[beacon.beacon_id] = beacon
        self._order.append(beacon.beacon_id)
        return self._emit(beacon)

    def edit(self, beacon_id: str, **changes) -> RadarBeacon:
        """[a: L2SYS-REQ-03.03] adjust a beacon's emitter parameters.

        [a: D2] "only the changed beacon is transmitted" -- exactly one message.
        """
        current = self._beacons[beacon_id]
        emitter_changes = {
            k: changes.pop(k) for k in list(changes)
            if k in ("pt_w", "gain_dbi", "frequency_hz", "pmin_dbm", "loss")
        }
        if emitter_changes:
            base = current.emitter or EmitterParameters()
            current = replace(current, emitter=replace(base, **emitter_changes))
        if changes:
            current = replace(current, **changes)
        self._beacons[beacon_id] = current
        return self._emit(current)

    def remove(self, beacon_id: str) -> RadarBeacon:
        """[a: D2] "Deletion is a tombstone (active=False)"."""
        beacon = self._beacons.pop(beacon_id)
        self._order.remove(beacon_id)
        return self._emit(beacon, active=False)

    # -- controlOwnshipRcs [a: fsim_TA_SEM.sysml] ----------------------
    def set_ownship_rcs(self, rcs_m2: float) -> list[RadarBeacon]:
        """[a: L2SYS-REQ-03.04] the ownship RCS as a scenario parameter.

        [a: D12] "a change of RCS recomputes and retransmits every beacon.
        Beacons without emitter parameters keep their explicit range and are
        not recomputed on an RCS change."
        """
        if rcs_m2 <= 0.0:
            raise ValueError("ownship RCS must be positive")
        self._rcs = float(rcs_m2)
        return [self._emit(b) for b in self.beacons if b.parameterized]

    # -- connectToSim / the received ownship state ---------------------
    def update_ownship(self, state: OwnshipState) -> list[RadarBeacon]:
        """[a: L2SYS-REQ-03.05] "against the received ownship state".

        Publishes only the beacons whose **detection status** changed, which is
        what makes the protocol event-driven rather than a periodic sweep.
        [a: D2]
        """
        previous = {
            b.beacon_id: self._detected_for(b, self._ownship)
            for b in self.beacons
        } if self._ownship is not None else {}
        self._ownship = state
        changed: list[RadarBeacon] = []
        for beacon in self.beacons:
            now = self._detected_for(beacon, state)
            if previous.get(beacon.beacon_id) != now:
                changed.append(self._emit(beacon))
        return changed

    def refresh_all(self) -> list[RadarBeacon]:
        """[a: D2] "Background refresh every 2.0 s." The whole roster, so a
        receiver that missed an event still converges.
        """
        return [self._emit(b) for b in self.beacons]

    # -- message construction ------------------------------------------
    def to_message(self, beacon: Beacon, active: bool = True) -> RadarBeacon:
        """[a: D1] the RadarBeacon fields."""
        max_range = beacon.max_range_m(self._rcs)
        slant = self._slant_for(beacon, self._ownship)
        return RadarBeacon(
            beacon_id=beacon.beacon_id,
            classification=beacon.classification,
            lat=beacon.lat, lon=beacon.lon, alt=beacon.alt,
            max_range_m=max_range,
            detected=(is_detected(slant, max_range) if active
                      and self._ownship is not None else False),
            slant_range_m=slant,
            active=active,
        )

    def _slant_for(self, beacon: Beacon, state: OwnshipState | None) -> float:
        if state is None:
            return 0.0
        return slant_range_m(state.lat_deg, state.lon_deg, state.alt_m,
                             beacon.lat, beacon.lon, beacon.alt)

    def _detected_for(self, beacon: Beacon, state: OwnshipState | None) -> bool:
        if state is None:
            return False
        return is_detected(self._slant_for(beacon, state),
                           beacon.max_range_m(self._rcs))

    def _emit(self, beacon: Beacon, active: bool = True) -> RadarBeacon:
        msg = self.to_message(beacon, active=active)
        self.published.append(msg)
        if self._publish is not None:
            self._publish(msg)
        return msg


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------
class ScenarioPublisher:
    """Sends RadarBeacon messages on the SEM's outbound port. [a: D4]

    connectToSim. [a: fsim_TA_SEM.sysml]
    """

    def __init__(
        self,
        host: str = ports.LOOPBACK,
        port: int = ports.SEM_TO_ORCH,
        sock: socket.socket | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._owns = sock is None
        self.sent = 0

    def __call__(self, beacon: RadarBeacon) -> None:
        try:
            self._sock.sendto(encode(beacon), (self.host, self.port))
            self.sent += 1
        except OSError:
            pass

    def close(self) -> None:
        if self._owns:
            try:
                self._sock.close()
            except OSError:
                pass


class OwnshipReceiver:
    """Receives OwnshipState on the SEM's inbound port. [a: D4] 5007.

    Stoppable via `stop_event`. [a: D15]
    """

    def __init__(
        self,
        on_state: Callable[[OwnshipState], None],
        bind_address: str = ports.BIND_ADDRESS,
        port: int = ports.ORCH_TO_SEM,
    ) -> None:
        self._on_state = on_state
        self.port = port
        self.stop_event = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind_address, port))
        self._sock.settimeout(0.05)
        self._thread: threading.Thread | None = None
        self.received = 0
        self.rejected = 0

    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self._run, name="sem-rx",
                                        daemon=True)
        self._thread.start()
        return self._thread

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                data, _ = self._sock.recvfrom(ports.MAX_DATAGRAM)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            state = messages.try_decode(data)
            if isinstance(state, OwnshipState):
                self.received += 1
                self._on_state(state)
            else:
                # a malformed or unexpected datagram must not kill the receiver
                self.rejected += 1

    def stop(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        try:
            self._sock.close()
        except OSError:
            pass


class RefreshTimer:
    """The D2 background refresh, driven by an injectable clock. [a: D2]

    "Background refresh every 2.0 s." Kept Qt-free and clock-injectable so the
    rate can be measured against a clock the suite controls. [a: D18]
    """

    def __init__(self, period_s: float = BEACON_REFRESH_S,
                 clock: Callable[[], float] | None = None) -> None:
        import time as _time

        self.period_s = period_s
        self._clock = clock or _time.monotonic
        self._next = self._clock() + period_s
        self.fires = 0

    def due(self) -> bool:
        now = self._clock()
        if now >= self._next:
            self._next = now + self.period_s
            self.fires += 1
            return True
        return False


def next_beacon_id(existing: Iterable[str], prefix: str = "EM") -> str:
    """A free id of the form EM1, EM2, ... [c] -- what would settle a different
    scheme: a stated beacon-id convention, which neither D1 nor D2 gives."""
    taken = set(existing)
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"
