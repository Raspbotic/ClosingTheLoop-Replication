"""BackgroundIOManager. [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml]

The TA gives this submodule three actions and four external ports:

    bridgeUdpData            -> _drain_hotas() / _drain_beacons() / publish()
    telemetryCaptureManager  -> TelemetryCapture, driven from tick()
    terrainElevationManager  -> poll_ground_elevation()

    e_hotasControlIn     5005   [a: D4]
    e_ownshipStateOut    5006   [a: D4]
    e_radarDetectionsIn  6006   [a: D4]
    e_groundElevMeterIn  8080   [a: D4]  (an HTTP endpoint the PCS connects to)

Qt-free: this is driven by `tick()`, which the main window calls from a QTimer
and the suite calls directly. That is what lets the beacon-staleness and
precedence rows run without an event loop. [a: D18]
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from typing import Callable

from flightsim.common import messages, ports
from flightsim.common.messages import (
    BEACON_STALE_S,
    HotasControls,
    OwnshipState,
    RadarBeacon,
    encode,
)
from flightsim.pcs.controls import (
    ControlSample,
    MirrorTimer,
    PrecedenceArbiter,
    SwitchEdgeDetector,
    ToggleState,
)

#: Rate at which the PCS publishes OwnshipState to the bus. [c] -- neither the
#: model nor the workbook states a publication rate; the 120 Hz of 01.02b
#: constrains the physics step, not the network. What would settle it: a stated
#: PCS->bus publication-rate requirement. Reported in docs/ARCHITECTURE.md.
PUBLISH_HZ = 20.0
PUBLISH_PERIOD_S = 1.0 / PUBLISH_HZ


@dataclass
class BeaconEntry:
    """One beacon in the PCS roster, with the time it was last heard. [a: D2]"""

    beacon: RadarBeacon
    last_seen_s: float


class UdpReceiver:
    """A bound, non-blocking receive socket drained by `drain()`.

    Non-blocking and drained on demand rather than on a thread, so a row can
    step the I/O deterministically. [b: D18]
    """

    def __init__(self, port: int, bind_address: str = ports.BIND_ADDRESS):
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setblocking(False)
        self._sock.bind((bind_address, port))
        self.received = 0
        self.rejected = 0

    def drain(self, limit: int = 256) -> list:
        """Decode everything waiting. A malformed datagram is counted and
        dropped, never raised. [a: D1]"""
        out = []
        for _ in range(limit):
            try:
                data, _addr = self._sock.recvfrom(ports.MAX_DATAGRAM)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            message = messages.try_decode(data)
            if message is None:
                self.rejected += 1
                continue
            self.received += 1
            out.append(message)
        return out

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class IOManager:
    """The PCS's background I/O. [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml]"""

    def __init__(
        self,
        host: str = ports.LOOPBACK,
        hotas_port: int = ports.HOTAS_TO_PCS,
        state_out_port: int = ports.PCS_TO_ORCH,
        beacon_port: int = ports.ORCH_TO_PCS,
        clock: Callable[[], float] | None = None,
        bind: bool = True,
        ground_elev_url: str | None = None,
        http_get: Callable[[str, float], object] | None = None,
    ) -> None:
        import time

        self.host = host
        self._clock = clock or time.monotonic

        # -- e_hotasControlIn 5005, e_radarDetectionsIn 6006 -----------
        self.hotas_rx = UdpReceiver(hotas_port) if bind else None
        self.beacon_rx = UdpReceiver(beacon_port) if bind else None
        # -- e_ownshipStateOut 5006 -------------------------------------
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.state_out_port = state_out_port
        self.published = 0
        self._next_publish = 0.0

        # -- arbitration -------------------------------------------------
        self.arbiter = PrecedenceArbiter(clock=self._clock)
        self.switches = SwitchEdgeDetector()
        self.mirror_timer = MirrorTimer(clock=self._clock)

        # -- beacons [a: D2] ---------------------------------------------
        self.beacons: dict[str, BeaconEntry] = {}

        # -- terrain [a: D9] ---------------------------------------------
        self.ground_elev_url = ground_elev_url
        self._http_get = http_get
        self.terrain_failures = 0

        self.messages: list[str] = []
        self.stop_event = threading.Event()

    # ------------------------------------------------------------------
    # bridgeUdpData
    # ------------------------------------------------------------------
    def drain_hotas(self) -> list[HotasControls]:
        """[a: D4] 5005. Physical input takes precedence for 1.0 s. [a: D10]"""
        if self.hotas_rx is None:
            return []
        got = [m for m in self.hotas_rx.drain() if isinstance(m, HotasControls)]
        for controls in got:
            self.arbiter.on_physical(controls)
            # [a: D7] toggle edges for btn2 and btn4 are detected HERE, at the
            # receiver; the transmitter sent raw pressed states.
            self.switches.apply(controls)
        return got

    def drain_beacons(self) -> list[RadarBeacon]:
        """[a: D4] 6006. [a: L2SYS-REQ-02.03]"""
        if self.beacon_rx is None:
            return []
        now = self._clock()
        got = [m for m in self.beacon_rx.drain() if isinstance(m, RadarBeacon)]
        for beacon in got:
            if not beacon.active:
                # [a: D2] "Deletion is a tombstone (active=False)."
                self.beacons.pop(beacon.beacon_id, None)
                continue
            self.beacons[beacon.beacon_id] = BeaconEntry(beacon, now)
        return got

    def expire_stale_beacons(self) -> list[str]:
        """[a: D2] "The receiver discards beacons stale for more than 5.0 s.\""""
        now = self._clock()
        gone = [
            bid for bid, entry in self.beacons.items()
            if (now - entry.last_seen_s) > BEACON_STALE_S
        ]
        for bid in gone:
            del self.beacons[bid]
        return gone

    @property
    def roster(self) -> list[RadarBeacon]:
        """[a: D2] "Every display of the beacon roster reflects both its
        insertions and its removals.\""""
        return [e.beacon for e in self.beacons.values()]

    def publish(self, state: OwnshipState, force: bool = False) -> bool:
        """[a: D4] 5006. Rate-limited to PUBLISH_HZ unless forced.

        `force` exists for D8's "publishes one telemetry frame ... even while
        flight-frozen".
        """
        now = self._clock()
        if not force and now < self._next_publish:
            return False
        self._next_publish = now + PUBLISH_PERIOD_S
        try:
            self._tx.sendto(encode(state), (self.host, self.state_out_port))
            self.published += 1
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # terrainElevationManager [a: D9]
    # ------------------------------------------------------------------
    def poll_ground_elevation(self) -> float | None:
        """[a: D9] [a: D4] "/json/position/ground-elev-m", the value arriving as
        a number, a string, or wrapped in an object under `value`.

        Returns None -- never raises -- when FlightGear is not configured or
        the request fails. "A failed poll is tolerated." [a: D9]
        """
        from flightsim.pcs.physics import parse_ground_elevation

        if not self.ground_elev_url:
            return None                     # no FlightGear: the poll is inactive
        getter = self._http_get or _default_http_get
        try:
            payload = getter(self.ground_elev_url, 0.5)
        except Exception:
            self.terrain_failures += 1
            return None
        value = parse_ground_elevation(payload)
        if value is None:
            self.terrain_failures += 1
        return value

    # ------------------------------------------------------------------
    # The commands that reach the aircraft
    # ------------------------------------------------------------------
    def resolve_controls(self, virtual: ControlSample | None = None
                         ) -> ControlSample:
        """[a: L2SYS-REQ-01.08b] [a: D10]"""
        if virtual is not None:
            self.arbiter.on_virtual(virtual)
        return self.arbiter.resolve()

    @property
    def physical_active(self) -> bool:
        return self.arbiter.physical_active

    @property
    def toggles(self) -> ToggleState:
        return self.switches.state

    @property
    def wheel_brake(self) -> float:
        return self.switches.wheel_brake

    # ------------------------------------------------------------------
    def tick(self) -> None:
        """One background pass: drain both sockets and expire stale beacons."""
        self.drain_hotas()
        self.drain_beacons()
        self.expire_stale_beacons()

    def close(self) -> None:
        self.stop_event.set()
        for rx in (self.hotas_rx, self.beacon_rx):
            if rx is not None:
                rx.close()
        try:
            self._tx.close()
        except OSError:
            pass


def _default_http_get(url: str, timeout: float):
    """GET a JSON document. Standard library only. [b: D4]"""
    import json
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
