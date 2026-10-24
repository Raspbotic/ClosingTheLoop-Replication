"""
tests/test_end_to_end.py
========================
R-9 end-to-end integration: orchestrator + PCS + SEM + demo HOTAS in one
process space over the real D4 ports.

Realizes the plan rows that require end-to-end observation, in particular
L2SYS-REQ-02.03 reception across the real bus, plus L2SYS-REQ-01.01b,
03.05 and 04.01 in their integrated form.

CAVEAT [a: D15]: these tests bind the real federation ports. Close any
running FlightSim instance (PCS, orchestrator, SEM, HOTAS) first.
All tests here are marked `slow`.
"""

from __future__ import annotations

import threading
import time

import pytest

from flightsim import hotas_standalone as hotas
from flightsim.common import ports
from flightsim.common.messages import OwnshipState, RadarBeacon
from flightsim.master_node import BusOrchestrator
from flightsim.pcs.main_window import BackgroundIOManager, PCSMainWindow
from flightsim.pcs.physics import ThreadPhysics
from flightsim.run_sem import SemCore

pytestmark = pytest.mark.slow


@pytest.fixture
def federation(qapp):
    """The whole federation in one process: orchestrator, PCS (with map),
    SEM. FlightGear and OSM are the two external processes of the TA and
    are not started here."""
    orch = BusOrchestrator().start()
    physics = ThreadPhysics(send_to_flightgear=False)
    io = BackgroundIOManager()
    pcs = PCSMainWindow(physics=physics, io_manager=io, with_map=True,
                        start_timers=False)
    sem = SemCore()
    sem.connect_to_sim()
    time.sleep(0.1)
    yield orch, pcs, sem
    pcs.close()
    io.close()
    physics.stop()
    sem.close()
    orch.stop()


def _pump(pcs, sem, seconds=1.5, publish=True):
    """Drive the PCS and SEM tick functions for a while (their QTimers are
    stopped so the test controls the schedule deterministically)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pcs.on_io_tick()
        if publish:
            pcs.on_publish_tick()
        sem.poll_ownship()
        time.sleep(0.02)


def test_e2e_ownship_state_reaches_the_sem(federation):
    """End-to-end realization of L2SYS-REQ-04.01 and the input side of
    L2SYS-REQ-03.05: a PCS-published OwnshipState crosses the real
    orchestrator (5006 -> 5007) and arrives at the SEM."""
    orch, pcs, sem = federation
    pcs.ssf_pause()
    pcs.airport_combo.setCurrentIndex(pcs.airport_combo.findData("KSFO"))
    expected = pcs.physics.telemetry()

    got = None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and got is None:
        pcs.on_publish_tick()
        got = sem.poll_ownship()
        time.sleep(0.02)

    assert got is not None, "no ownship state reached the SEM"
    assert got.lat_deg == pytest.approx(expected.lat_deg, abs=0.05)
    assert orch.counts["pcs->se"] >= 1


def test_e2e_demo_hotas_controls_reach_the_pcs(federation):
    """End-to-end realization of L2SYS-REQ-01.01b and 01.08b: the --demo
    HOTAS source transmits on 5005 and the PCS I/O manager takes it as the
    physical input, greying out the virtual HOTAS [a: D10]."""
    orch, pcs, sem = federation
    stop = threading.Event()
    thread = threading.Thread(
        target=hotas.run, args=(hotas.DemoSource(),),
        kwargs=dict(rate_hz=30.0, stop_event=stop),
        daemon=True, name="hotas-e2e")
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and pcs.io.last_physical is None:
            pcs.on_io_tick()
            time.sleep(0.02)
        assert pcs.io.last_physical is not None, \
            "demo HOTAS controls never reached the PCS"
        pcs.on_io_tick()
        assert pcs.virtual_hotas.physical_active is True
        assert "LOCKED" in pcs.virtual_hotas.title()
        assert pcs.io.last_physical.throttle == pytest.approx(0.75)
    finally:
        stop.set()
        thread.join(timeout=3.0)
    assert not thread.is_alive()


def test_e2e_beacon_reaches_pcs_and_map_with_correct_detection(federation):
    """End-to-end realization of L2SYS-REQ-02.03 ("the PCS shall receive
    the threat detections incoming from the Synthetic Environment") across
    the real bus, together with 02.03b (the map receives it) and 03.05
    (the detection status computed from the ownship state)."""
    orch, pcs, sem = federation
    pcs.ssf_pause()
    _pump(pcs, sem, 1.0)                     # let the SEM learn the ownship
    assert sem.ownship.lat_deg != 0.0

    near = sem.add_beacon(classification="FOE", range_m=5_000.0,
                          bearing_deg=45.0)
    far = sem.add_beacon(classification="FRIEND", range_m=400_000.0,
                         bearing_deg=180.0)
    assert near.detected is True             # inside the computed R_max
    assert far.detected is False

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and len(pcs.io.beacons) < 2:
        _pump(pcs, sem, 0.2)
    assert len(pcs.io.beacons) == 2, "beacons did not reach the PCS"

    received_near = pcs.io.beacons.get(near.beacon_id)
    received_far = pcs.io.beacons.get(far.beacon_id)
    assert received_near.classification == "FOE"
    assert received_near.detected is True
    assert received_far.detected is False
    assert received_near.max_range_m == pytest.approx(
        near.max_range_m(sem.ownship_rcs_m2), rel=1e-9)

    # ... and onto the tactical map (02.03b)
    pcs.on_publish_tick()
    assert near.beacon_id in pcs.map_window.mapGuiWorker.beacons
    image = pcs.map_window.i_2dTacticalMapOut()
    assert not image.isNull()


def test_e2e_tombstone_clears_the_pcs_and_the_map(federation):
    """End-to-end [a: D2]: deleting a beacon in the SEM removes it from the
    PCS store and from the map."""
    orch, pcs, sem = federation
    pcs.ssf_pause()
    _pump(pcs, sem, 0.6)
    beacon = sem.add_beacon(classification="UNKNOWN", range_m=8_000.0)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and beacon.beacon_id not in pcs.io.beacons:
        _pump(pcs, sem, 0.2)
    assert beacon.beacon_id in pcs.io.beacons
    pcs.on_publish_tick()
    assert beacon.beacon_id in pcs.map_window.mapGuiWorker.beacons

    sem.remove_beacon(beacon.beacon_id)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and beacon.beacon_id in pcs.io.beacons:
        _pump(pcs, sem, 0.2)

    assert beacon.beacon_id not in pcs.io.beacons, "tombstone did not clear the PCS"
    pcs.on_publish_tick()
    assert beacon.beacon_id not in pcs.map_window.mapGuiWorker.beacons


def test_e2e_rcs_change_propagates_to_the_pcs(federation):
    """End-to-end realization of L2SYS-REQ-03.04 + [a: D12]: changing the
    scenario RCS in the SEM recomputes every beacon and the new ranges
    reach the PCS across the bus."""
    orch, pcs, sem = federation
    pcs.ssf_pause()
    _pump(pcs, sem, 0.6)
    beacon = sem.add_beacon(classification="FOE", range_m=10_000.0)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and beacon.beacon_id not in pcs.io.beacons:
        _pump(pcs, sem, 0.2)
    before = pcs.io.beacons.get(beacon.beacon_id).max_range_m

    sem.set_ownship_rcs(16.0 * sem.ownship_rcs_m2)   # 16x RCS -> 2x range
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        _pump(pcs, sem, 0.2)
        now = pcs.io.beacons.get(beacon.beacon_id)
        if now is not None and now.max_range_m > before * 1.5:
            break
    after = pcs.io.beacons.get(beacon.beacon_id).max_range_m
    assert after == pytest.approx(2.0 * before, rel=1e-6)


def test_e2e_full_loop_stays_green_under_continuous_traffic(federation):
    """R-9 acceptance: the federation runs for a few seconds with all
    links active and no thread dies. Realizes L2SYS-REQ-04.01 under load
    together with the PCS 10 Hz publication rate."""
    orch, pcs, sem = federation
    pcs.ssf_pause()
    stop = threading.Event()
    thread = threading.Thread(
        target=hotas.run, args=(hotas.DemoSource(),),
        kwargs=dict(rate_hz=60.0, stop_event=stop),
        daemon=True, name="hotas-e2e-load")
    thread.start()
    try:
        for i in range(3):
            sem.add_beacon(classification=["FOE", "FRIEND", "UNKNOWN"][i],
                           range_m=6_000.0 * (i + 1), bearing_deg=90.0 * i)
        _pump(pcs, sem, 3.0)
        assert len(pcs.io.beacons) == 3
        assert orch.counts["pcs->se"] > 0 and orch.counts["se->pcs"] > 0
        assert len(pcs.io.published) > 0
        assert pcs.io.last_physical is not None
    finally:
        stop.set()
        thread.join(timeout=3.0)
