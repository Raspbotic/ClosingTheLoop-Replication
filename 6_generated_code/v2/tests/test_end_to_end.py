"""End-to-end federation. [a: R-9]

"an end-to-end test: orchestrator, PCS, SEM and demo HOTAS in one process
space over the real ports, asserting that ownship state reaches the SEM, demo
controls reach the PCS, an injected beacon reaches the PCS and the map with
correct detection, and a tombstone clears both (marked `slow`)."

Marked `slow` per D15. Run the fast suite with `-m "not slow"`.

CAVEAT per D15: this module binds every port of D4. Close any running instance
of the application first.

No FlightGear, no joystick, no external network: the HOTAS runs from its
hardware-free demo source, the map's fetcher is pointed at a stub that always
fails, and FlightGear is not configured. [a: Sec.3 criterion 6]
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from flightsim import hotas_standalone as hotas
from flightsim.common import messages, ports
from flightsim.master_node import BusOrchestrator
from flightsim.pcs.airports import SEED_AIRPORTS
from flightsim.pcs.io_manager import IOManager
from flightsim.pcs.main_window import PcsPaths, build_main_window
from flightsim.pcs.physics import PhysicsThread
from flightsim.sem_model import (
    Beacon,
    EmitterParameters,
    OwnshipReceiver,
    Scenario,
    ScenarioPublisher,
)

pytestmark = pytest.mark.slow


class Federation:
    """The four processes, in one process space, over the real D4 ports."""

    def __init__(self, tmp_path, clock, qt_widgets):
        self.clock = clock
        # -- orchestrator: 5006 -> 5007 and 6007 -> 6006 -----------------
        self.orchestrator = BusOrchestrator()
        self.orchestrator.start()

        # -- SEM: receives on 5007, publishes on 6007 --------------------
        self.sem_states: list = []
        self.publisher = ScenarioPublisher()
        self.scenario = Scenario(publish=self.publisher)
        self.receiver = OwnshipReceiver(self._on_ownship)
        self.receiver.start()

        # -- PCS: 5005 in, 5006 out, 6006 in -----------------------------
        paths = PcsPaths(
            telemetry_dir=str(tmp_path / "telemetry"),
            snapshot_dir=str(tmp_path / "snapshots"),
            tile_cache_dir=str(tmp_path / "tiles"),
            airports_dir=str(tmp_path),
        )
        physics = PhysicsThread(airport=SEED_AIRPORTS[0], emit_fdm=False)
        io = IOManager()
        self.pcs = qt_widgets(build_main_window(
            paths, physics=physics, io=io, tile_fetch=lambda url: None,
        ))

        # -- HOTAS: the hardware-free demo source, transmitting on 5005 --
        self.hotas_stop = threading.Event()
        self.transmitter = hotas.Transmitter(hotas.DemoDeviceSource())
        self.hotas_thread = threading.Thread(
            target=self.transmitter.run,
            kwargs={"stop_event": self.hotas_stop},
            name="e2e-hotas", daemon=True,
        )

    def _on_ownship(self, state) -> None:
        self.sem_states.append(state)
        self.scenario.update_ownship(state)

    def start_hotas(self) -> None:
        self.hotas_thread.start()

    def pump(self, seconds: float = 1.0, step: float = 0.01) -> None:
        """Run the PCS's periodic pass for a wall-clock window.

        Wall clock here, not the injected one: the row is waiting for the
        operating system's network stack to move datagrams between four real
        sockets, which no injected clock controls. Nothing is *measured* from
        the elapsed time; it is only a bound. [b: D18]
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.pcs.tick()
            time.sleep(step)

    def wait_until(self, predicate, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pcs.tick()
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def close(self) -> None:
        self.hotas_stop.set()
        if self.hotas_thread.is_alive():
            self.hotas_thread.join(timeout=3.0)
        self.transmitter.close()
        self.receiver.stop()
        self.publisher.close()
        self.orchestrator.stop()


@pytest.fixture
def federation(tmp_path, clock, qt_widgets):
    fed = Federation(tmp_path, clock, qt_widgets)
    yield fed
    fed.close()


def send(data: bytes, port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(data, ("127.0.0.1", port))
    finally:
        s.close()


# ===========================================================================
def test_ownship_state_reaches_the_sem(federation):
    """R-9. PCS -> 5006 -> orchestrator -> 5007 -> SEM.
    [a: L2SYS-REQ-04.01] [a: D3] [a: D4]
    """
    federation.pcs.ssf_run()
    assert federation.wait_until(lambda: len(federation.sem_states) >= 3), (
        "no ownship state reached the SEM across the bus"
    )
    state = federation.sem_states[-1]
    assert state.model == "f16"
    assert state.lat_deg == pytest.approx(SEED_AIRPORTS[0].lat_deg, abs=0.5)
    # the relay actually carried them
    assert federation.orchestrator.pcsNode.stats.forwarded >= 3


def test_demo_controls_reach_the_pcs(federation):
    """R-9. HOTAS -> 5005 -> PCS, from the hardware-free demo source.
    [a: L2SYS-REQ-01.01b] [a: L2SYS-REQ-01.08b] [a: D10]
    """
    federation.pcs.ssf_run()
    federation.start_hotas()

    assert federation.wait_until(lambda: federation.pcs.io.physical_active), (
        "no HOTAS datagram reached the PCS"
    )
    # [a: D10] the widget greys out and its title changes while locked
    assert federation.pcs.virtual_hotas.locked is True
    assert federation.pcs.io.hotas_rx.received > 0

    # and the datagram's values are what the aircraft is flying
    sample = federation.pcs.io.arbiter.physical_sample
    command = federation.pcs.physics.commands.get()
    assert command.throttle == pytest.approx(sample.throttle, abs=1e-6)


def test_injected_beacon_reaches_the_pcs_and_the_map_with_correct_detection(
    federation
):
    """R-9. SEM -> 6007 -> orchestrator -> 6006 -> PCS -> map.
    [a: L2SYS-REQ-02.03] [a: L2SYS-REQ-02.03b] [a: L2SYS-REQ-03.05] [a: D12]
    """
    federation.pcs.ssf_run()
    assert federation.wait_until(lambda: len(federation.sem_states) >= 2)
    own = federation.sem_states[-1]

    # A beacon close enough to be detected: at the ownship's own position, so
    # the slant range is ~0 and well inside R_max at the D12 defaults (~38.8 km).
    federation.scenario.add(Beacon(
        beacon_id="E2E-NEAR", classification="FOE",
        lat=own.lat_deg, lon=own.lon_deg, alt=0.0,
        emitter=EmitterParameters(),
    ))
    # And one far outside R_max: 5 degrees of latitude is about 555 km.
    federation.scenario.add(Beacon(
        beacon_id="E2E-FAR", classification="FRIEND",
        lat=own.lat_deg + 5.0, lon=own.lon_deg, alt=0.0,
        emitter=EmitterParameters(),
    ))

    assert federation.wait_until(
        lambda: {b.beacon_id for b in federation.pcs.io.roster}
        >= {"E2E-NEAR", "E2E-FAR"}
    ), f"beacons did not reach the PCS: {federation.pcs.io.roster}"

    by_id = {b.beacon_id: b for b in federation.pcs.io.roster}
    assert by_id["E2E-NEAR"].detected is True
    assert by_id["E2E-FAR"].detected is False
    assert by_id["E2E-NEAR"].max_range_m == pytest.approx(38792.0, rel=0.01)

    # and both reach the map, with the ring styles D24 gives them
    federation.pump(0.2)
    plan = federation.pcs.map.mapGuiWorker.render_plan()
    rings = {i["beacon_id"]: i for i in plan if i["kind"] == "ring"}
    assert set(rings) == {"E2E-NEAR", "E2E-FAR"}
    assert rings["E2E-NEAR"]["style"] == "solid"        # detected
    assert rings["E2E-FAR"]["style"] == "dashed"        # undetected [a: D24]
    assert rings["E2E-NEAR"]["colour"] != rings["E2E-FAR"]["colour"]


def test_a_tombstone_clears_the_pcs_and_the_map(federation):
    """R-9. [a: D2] "Deletion is a tombstone (active=False)", and it clears
    both the roster and the map.
    """
    federation.pcs.ssf_run()
    assert federation.wait_until(lambda: len(federation.sem_states) >= 2)
    own = federation.sem_states[-1]

    federation.scenario.add(Beacon(
        beacon_id="E2E-DOOMED", classification="UNKNOWN",
        lat=own.lat_deg, lon=own.lon_deg, alt=0.0,
        emitter=EmitterParameters(),
    ))
    assert federation.wait_until(
        lambda: any(b.beacon_id == "E2E-DOOMED"
                    for b in federation.pcs.io.roster)
    )
    federation.pump(0.2)
    assert any(i.get("beacon_id") == "E2E-DOOMED"
               for i in federation.pcs.map.mapGuiWorker.render_plan())

    federation.scenario.remove("E2E-DOOMED")

    assert federation.wait_until(
        lambda: not any(b.beacon_id == "E2E-DOOMED"
                        for b in federation.pcs.io.roster)
    ), "the tombstone did not clear the PCS roster"
    federation.pump(0.2)
    assert not any(i.get("beacon_id") == "E2E-DOOMED"
                   for i in federation.pcs.map.mapGuiWorker.render_plan()), (
        "the tombstone did not clear the map"
    )


def test_the_whole_federation_runs_with_no_flightgear(federation):
    """R-9. [a: Sec.3 criterion 6] The federation flies with nothing on 5550
    and no FlightGear configured.
    """
    assert federation.pcs.flightgear.resolved is False
    federation.pcs.ssf_run()
    federation.pcs.btn_engine.click()
    federation.pcs.virtual_hotas.throttle.setValue(80)
    federation.pump(1.0)

    assert federation.pcs.last_state.sim_time_s > 0.0
    # LAUNCH 3D OTW reports and does nothing, and the sim keeps running
    before = federation.pcs.last_state.sim_time_s
    federation.pcs.ssf_launch_otw()
    federation.pump(0.5)
    assert federation.pcs.last_state.sim_time_s > before
    assert "FlightGear was not found" in federation.pcs.log_text


def test_the_relay_is_what_carries_the_traffic(federation):
    """R-9 / P-04.01-8. [a: D3] "The relay is an intermediate process: SEM and
    PCS never communicate directly." Stopping the relay stops the flow.
    """
    federation.pcs.ssf_run()
    assert federation.wait_until(lambda: len(federation.sem_states) >= 3)

    federation.orchestrator.stop()
    federation.sem_states.clear()
    federation.pump(1.0)

    assert federation.sem_states == [], (
        "ownship state reached the SEM with the relay stopped"
    )
