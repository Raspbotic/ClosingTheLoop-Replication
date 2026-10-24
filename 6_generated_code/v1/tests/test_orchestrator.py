"""
tests/test_orchestrator.py
==========================
Verification-plan rows allocated to the bus orchestrator:
L2SYS-REQ-04.01 and the D3 conformance rows.

[a: D15] the relay thread is stopped in teardown so no test leaks
datagrams into a later test.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from flightsim.common import ports
from flightsim.common.messages import OwnshipState, RadarBeacon
from flightsim.master_node import BusOrchestrator


@pytest.fixture
def orchestrator():
    """A running orchestrator on the real D4 ports, stopped in teardown."""
    orch = BusOrchestrator().start()
    time.sleep(0.05)
    yield orch
    orch.stop()


@pytest.fixture
def sender():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yield sock
    sock.close()


# ==========================================================================
# L2SYS-REQ-04.01 - relay both directions
# ==========================================================================

def test_04_01_relays_ownship_state_from_pcs_to_sem(orchestrator, sender,
                                                    udp_receiver):
    """L2SYS-REQ-04.01: the BusManager shall orchestrate the exchange of
    state variables between the tactical databus and external UDP ports.

    Stimulus: an OwnshipState datagram into pcsNode.e_ownshipStateIn (5006).
    Observation: seNode.e_ownshipStateOut (5007), the SEM's inbound port.
    Pass: the datagram arrives byte-identical.
    """
    rx = udp_receiver(ports.UDP_ORCH_TO_SEM)
    payload = OwnshipState(lat_deg=45.44, lon_deg=9.28, alt_m=900.0,
                           speed_mps=120.0, seq=5).to_json()
    sender.sendto(payload, (ports.LOCALHOST, ports.UDP_PCS_TO_ORCH))
    assert rx.wait_for(1), "ownship state not relayed to the SEM port"
    assert rx.datagrams[0] == payload
    assert OwnshipState.from_json(rx.datagrams[0]).lat_deg == pytest.approx(45.44)


def test_04_01_relays_beacon_from_sem_to_pcs(orchestrator, sender,
                                             udp_receiver):
    """L2SYS-REQ-04.01, the reverse path.

    Stimulus: a RadarBeacon into seNode.e_radarDetectionsIn (6007).
    Observation: pcsNode.e_radarDetectionsOut (6006).
    """
    rx = udp_receiver(ports.UDP_ORCH_TO_PCS)
    payload = RadarBeacon(beacon_id=3, classification="FOE",
                          lat_deg=45.5, lon_deg=9.4, max_range_m=30000.0,
                          slant_range_m=12000.0, detected=True).to_json()
    sender.sendto(payload, (ports.LOCALHOST, ports.UDP_SEM_TO_ORCH))
    assert rx.wait_for(1), "beacon not relayed to the PCS port"
    assert rx.datagrams[0] == payload


def test_04_01_relay_is_payload_opaque(orchestrator, sender, udp_receiver):
    """L2SYS-REQ-04.01 + [a: D3] "payload-opaque": the orchestrator
    forwards bytes it cannot decode, so it can never corrupt or reject a
    message whose format it does not know."""
    rx = udp_receiver(ports.UDP_ORCH_TO_SEM)
    junk = b"\x00\x01\x02 not a D1 message at all \xff"
    sender.sendto(junk, (ports.LOCALHOST, ports.UDP_PCS_TO_ORCH))
    assert rx.wait_for(1)
    assert rx.datagrams[0] == junk


def test_04_01_tombstone_transits_unmodified(orchestrator, sender,
                                             udp_receiver):
    """L2SYS-REQ-04.01 + [a: D2]: a deletion tombstone must survive the
    relay unchanged, otherwise the PCS could never delete a beacon."""
    rx = udp_receiver(ports.UDP_ORCH_TO_PCS)
    live = RadarBeacon(beacon_id=8, classification="UNKNOWN",
                       max_range_m=15000.0)
    payload = live.tombstone().to_json()
    sender.sendto(payload, (ports.LOCALHOST, ports.UDP_SEM_TO_ORCH))
    assert rx.wait_for(1)
    got = RadarBeacon.from_json(rx.datagrams[0])
    assert got.is_tombstone is True and got.beacon_id == 8


def test_04_01_both_directions_are_independent(orchestrator, sender,
                                               udp_receiver):
    """L2SYS-REQ-04.01: the two internal links (i_Pcs2Se* and i_Se2Pcs*)
    carry traffic simultaneously without cross-talk."""
    to_sem = udp_receiver(ports.UDP_ORCH_TO_SEM)
    to_pcs = udp_receiver(ports.UDP_ORCH_TO_PCS)
    a = OwnshipState(lat_deg=1.0).to_json()
    b = RadarBeacon(beacon_id=1, classification="FRIEND").to_json()
    sender.sendto(a, (ports.LOCALHOST, ports.UDP_PCS_TO_ORCH))
    sender.sendto(b, (ports.LOCALHOST, ports.UDP_SEM_TO_ORCH))
    assert to_sem.wait_for(1) and to_pcs.wait_for(1)
    assert to_sem.datagrams == [a], "PCS traffic leaked to the PCS port"
    assert to_pcs.datagrams == [b], "SEM traffic leaked to the SEM port"


@pytest.mark.slow
def test_04_01_non_blocking_under_a_datagram_burst(orchestrator, sender,
                                                   udp_receiver):
    """L2SYS-REQ-04.01: "... without blocking the Python execution
    threads."

    Stimulus: a burst of 500 datagrams into 5006.
    Observation: the relay thread and the wall clock.
    Pass: the burst is drained, the relay thread is still alive and
    responsive afterwards, and no call blocked for the burst duration.
    """
    rx = udp_receiver(ports.UDP_ORCH_TO_SEM)
    n = 500
    t0 = time.monotonic()
    for i in range(n):
        sender.sendto(OwnshipState(seq=i).to_json(),
                      (ports.LOCALHOST, ports.UDP_PCS_TO_ORCH))
    send_elapsed = time.monotonic() - t0
    assert send_elapsed < 2.0, "sending blocked on the orchestrator"

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and orchestrator.counts["pcs->se"] < n:
        time.sleep(0.02)

    # UDP may legitimately drop under burst; the requirement is about not
    # blocking, so the criterion is progress + liveness, not zero loss.
    assert orchestrator.counts["pcs->se"] >= 1
    assert len(rx.datagrams) >= 1

    # still responsive after the burst
    rx.clear()
    marker = OwnshipState(seq=999999).to_json()
    sender.sendto(marker, (ports.LOCALHOST, ports.UDP_PCS_TO_ORCH))
    assert rx.wait_for(1, timeout=2.0), "relay stopped responding after burst"


def test_04_01_poll_returns_promptly_with_no_traffic(orchestrator):
    """L2SYS-REQ-04.01: with an idle bus the loop must not block longer
    than its own timeout."""
    t0 = time.monotonic()
    forwarded = orchestrator.poll(timeout=0.05)
    elapsed = time.monotonic() - t0
    assert forwarded == 0
    assert elapsed < 0.5


# ==========================================================================
# D3 - orchestrator construction and shutdown
# ==========================================================================

def test_d3_is_self_contained_with_no_external_middleware():
    """[a: D3] "Self-contained Python selectors UDP relay (no external
    middleware)"."""
    import flightsim.master_node as mn
    import inspect
    src = inspect.getsource(mn)
    assert "import selectors" in src
    for forbidden in ("zmq", "pika", "kafka", "redis", "dds", "mqtt"):
        assert forbidden not in src.lower(), \
            f"external middleware {forbidden!r} referenced"


def test_d3_binds_exactly_the_d4_ports():
    """[a: D3][a: D4] the two internal nodes bind the TA node ports."""
    orch = BusOrchestrator()
    try:
        assert orch._pcs_in_sock.getsockname()[1] == ports.UDP_PCS_TO_ORCH
        assert orch._se_in_sock.getsockname()[1] == ports.UDP_SEM_TO_ORCH
        assert orch._se_out_addr[1] == ports.UDP_ORCH_TO_SEM
        assert orch._pcs_out_addr[1] == ports.UDP_ORCH_TO_PCS
    finally:
        orch.stop()


def test_d3_sockets_are_non_blocking():
    """[a: D3] "non-blocking"; L2SYS-REQ-04.01 forbids blocking the
    Python execution threads."""
    orch = BusOrchestrator()
    try:
        for sock in (orch._pcs_in_sock, orch._se_in_sock, orch._out_sock):
            assert sock.gettimeout() == 0.0, "socket is not non-blocking"
    finally:
        orch.stop()


def test_d3_stop_is_clean_and_idempotent():
    """[a: D3] "clean OSError-tolerant shutdown (Windows teardown race)":
    stop() may be called twice and leaves no live thread."""
    orch = BusOrchestrator().start()
    time.sleep(0.05)
    orch.stop()
    orch.stop()                                   # must not raise
    assert orch._thread is not None and not orch._thread.is_alive()
    assert "orch-relay" not in {t.name for t in threading.enumerate()}


def test_d3_context_manager_shuts_down():
    """[a: D3] the context-manager form releases the ports."""
    with BusOrchestrator() as orch:
        assert orch.poll(timeout=0.01) == 0
    # the ports must be free again immediately afterwards
    again = BusOrchestrator()
    again.stop()
