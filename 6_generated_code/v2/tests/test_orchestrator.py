"""BusOrchestrator.

This module verifies L2SYS-REQ-04.01 throughout: "The BusManager shall
orchestrate the exchange of state variables between the tactical databus and
external UDP ports without blocking the Python execution threads."

Plan rows: P-04.01-1 .. P-04.01-9.

These rows bind the real D4 ports. CAVEAT per D15: close any running instance of
the application before running the suite.
"""

from __future__ import annotations

import ast
import inspect
import socket
import sys
import threading
import time

import pytest

from flightsim import master_node
from flightsim.common import messages, ports
from flightsim.master_node import BusOrchestrator

# [a: D4], transcribed.
D4_PCS_TO_ORCH = 5006
D4_ORCH_TO_SEM = 5007
D4_SEM_TO_ORCH = 6007
D4_ORCH_TO_PCS = 6006


@pytest.fixture
def relay():
    orch = BusOrchestrator()
    orch.start()
    yield orch
    orch.stop()


def send(data: bytes, port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(data, ("127.0.0.1", port))
    finally:
        s.close()


# ===========================================================================
# P-04.01-1 / -2 -- forwarding, both directions
# ===========================================================================
def test_forwards_ownship_state_5006_to_5007(relay, udp_sink):
    """P-04.01-1. PCS -> orchestrator -> SEM, bytes identical. [a: D4]"""
    sink = udp_sink(D4_ORCH_TO_SEM)
    payload = b'{"kind":"ownship","probe":1}'
    send(payload, D4_PCS_TO_ORCH)
    assert sink.wait_for(1), "nothing reached 5007"
    assert sink.datagrams[0] == payload


def test_forwards_radar_detections_6007_to_6006(relay, udp_sink):
    """P-04.01-2. SEM -> orchestrator -> PCS, bytes identical. [a: D4]"""
    sink = udp_sink(D4_ORCH_TO_PCS)
    payload = b'{"kind":"beacon","probe":2}'
    send(payload, D4_SEM_TO_ORCH)
    assert sink.wait_for(1), "nothing reached 6006"
    assert sink.datagrams[0] == payload


def test_the_two_directions_do_not_cross(relay, udp_sink):
    """P-04.01-1/-2. A datagram sent one way must not appear on the other
    direction's sink -- the classic wiring mistake the TA's node structure
    exists to prevent. [a: fsim_TA_Orch.sysml]
    """
    to_sem = udp_sink(D4_ORCH_TO_SEM)
    to_pcs = udp_sink(D4_ORCH_TO_PCS)
    send(b"from-the-pcs", D4_PCS_TO_ORCH)
    assert to_sem.wait_for(1)
    time.sleep(0.1)
    assert to_pcs.datagrams == []
    assert to_sem.datagrams == [b"from-the-pcs"]


def test_ports_used_are_the_d4_ports(relay):
    """P-04.01-1/-2. The relay binds exactly the D4 ports. [a: D4]"""
    assert relay.pcsNode.inbound_port == D4_PCS_TO_ORCH
    assert relay.seNode.outbound_port == D4_ORCH_TO_SEM
    assert relay.seNode.inbound_port == D4_SEM_TO_ORCH
    assert relay.pcsNode.outbound_port == D4_ORCH_TO_PCS


# ===========================================================================
# P-04.01-3 -- payload opacity
# ===========================================================================
@pytest.mark.parametrize("payload", [
    b"not json at all",
    b"\x00\x01\x02\xff\xfe",
    b'{"kind":"a-message-type-that-does-not-exist","x":1}',
    b"",
    b"x" * 1400,
])
def test_forwarding_is_payload_opaque(relay, udp_sink, payload):
    """P-04.01-3. [a: D3] "payload-opaque". A payload the wire format would
    refuse still crosses the bus unchanged -- the relay does not decode.
    """
    sink = udp_sink(D4_ORCH_TO_SEM)
    send(payload, D4_PCS_TO_ORCH)
    assert sink.wait_for(1), f"opaque payload {payload[:20]!r} did not cross"
    assert sink.datagrams[0] == payload


def test_a_payload_the_codec_rejects_still_crosses(relay, udp_sink):
    """P-04.01-3. Stated as a property of the two together: the codec refuses
    this payload, and the relay forwards it anyway. [a: D3] [a: D1]
    """
    payload = b'{"kind":"hotas","roll":99.0}'
    with pytest.raises(messages.WireFormatError):
        messages.decode(payload)
    sink = udp_sink(D4_ORCH_TO_SEM)
    send(payload, D4_PCS_TO_ORCH)
    assert sink.wait_for(1)
    assert sink.datagrams[0] == payload


# ===========================================================================
# P-04.01-4 / -5 -- non-blocking
# ===========================================================================
def test_sockets_are_non_blocking(relay):
    """P-04.01-4. [a: D3] "non-blocking"; [a: L2SYS-REQ-04.01] "without
    blocking the Python execution threads".
    """
    for node in relay.nodes:
        assert node.socket is not None, node.name
        assert node.socket.gettimeout() == 0.0, (
            f"{node.name} socket is in blocking mode"
        )
    assert relay._tx.gettimeout() == 0.0


def test_relay_thread_makes_progress_with_no_traffic(relay, udp_sink):
    """P-04.01-5. Idling on an empty bus does not wedge the loop: after a
    second of silence a datagram still crosses, and the thread never died.
    """
    sink = udp_sink(D4_ORCH_TO_SEM)
    assert relay._thread.is_alive()
    time.sleep(1.0)
    assert relay._thread.is_alive(), "relay thread died while idle"
    send(b"after-the-silence", D4_PCS_TO_ORCH)
    assert sink.wait_for(1)


def test_other_threads_keep_running_while_the_relay_works(relay, udp_sink):
    """P-04.01-4. "without blocking the Python execution threads": a second
    thread must keep making progress while the bus is busy.
    [a: L2SYS-REQ-04.01]
    """
    sink = udp_sink(D4_ORCH_TO_SEM)
    ticks = []
    stop = threading.Event()
    WINDOW_S = 0.5
    TICK_S = 0.005

    def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            time.sleep(TICK_S)

    def burster():
        i = 0
        deadline = time.monotonic() + WINDOW_S
        while time.monotonic() < deadline:
            send(f"burst-{i}".encode(), D4_PCS_TO_ORCH)
            i += 1
            time.sleep(0.001)

    t = threading.Thread(target=ticker, name="ticker")
    b = threading.Thread(target=burster, name="burster")
    t.start()
    b.start()
    try:
        b.join(timeout=WINDOW_S + 2.0)
    finally:
        stop.set()
        t.join(timeout=2.0)

    # In WINDOW_S seconds a 5 ms ticker should get ~100 turns. Requiring 40
    # leaves generous margin for scheduling while still failing outright if the
    # relay held the GIL or blocked on a socket for the whole window.
    ideal = WINDOW_S / TICK_S
    assert len(ticks) > 0.4 * ideal, (
        f"the other thread ran only {len(ticks)} times in {WINDOW_S} s "
        f"(ideal ~{ideal:.0f}) -- the relay is blocking other threads"
    )
    # and the relay was actually working during that window
    assert relay.pcsNode.stats.forwarded > 50, relay.pcsNode.stats


# ===========================================================================
# P-04.01-6 -- stoppable
# ===========================================================================
def test_stop_returns_promptly():
    """P-04.01-6. [a: D3] "stoppable on Windows". stop() returns within 2 s and
    the thread is not alive afterwards. [a: D15]
    """
    orch = BusOrchestrator()
    thread = orch.start()
    assert thread.is_alive()

    started = time.monotonic()
    orch.stop(timeout=2.0)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"stop() took {elapsed:.2f} s"
    assert not thread.is_alive()


def test_stop_does_not_close_a_socket_from_another_thread():
    """P-04.01-6. The Windows-safe shutdown is a property of *how* it stops:
    the loop wakes on its own select timeout and closes its own sockets, rather
    than another thread closing a socket underneath it. Asserted on the source,
    because the failure mode it prevents does not reproduce on Linux. [a: D3]
    """
    tree = ast.parse(inspect.getsource(BusOrchestrator.stop).lstrip())
    func = tree.body[0]
    body = func.body[1:] if (            # drop the docstring
        func.body and isinstance(func.body[0], ast.Expr)
        and isinstance(func.body[0].value, ast.Constant)
    ) else func.body
    code = "\n".join(ast.dump(node) for node in body)

    assert "close" not in code, (
        "stop() must not close sockets; the loop closes its own in run()'s "
        "finally block, which is what makes it safe on Windows"
    )
    assert "stop_event" in code and "set" in code
    # and run() is where the closing happens
    assert "self.close()" in inspect.getsource(BusOrchestrator.run)


def test_restart_after_stop_works():
    """P-04.01-6. Stopping releases the ports, so the relay can be started
    again -- which is also what keeps the suite's own rows independent. [b: D3]
    """
    orch = BusOrchestrator()
    orch.start()
    orch.stop()
    orch2 = BusOrchestrator()
    thread = orch2.start()
    assert thread.is_alive()
    orch2.stop()


# ===========================================================================
# P-04.01-7 -- standard library only
# ===========================================================================
def test_module_imports_only_the_standard_library():
    """P-04.01-7. [a: D3] "no external middleware". R-6 acceptance: "the module
    imports only the standard library."
    """
    tree = ast.parse(inspect.getsource(master_node))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # relative import
                continue
            imported.append((node.module or "").split(".")[0])

    stdlib = set(sys.stdlib_module_names)
    for name in imported:
        if name == "flightsim":                  # this project's own package
            continue
        assert name in stdlib, f"{name!r} is not in the standard library"

    # and specifically none of the usual middleware
    for forbidden in ("zmq", "pika", "kombu", "redis", "paho", "nats",
                      "rti", "cyclonedds", "opensplice", "grpc"):
        assert forbidden not in imported


def test_the_relay_uses_selectors():
    """P-04.01-7. [a: D3] "Self-contained Python `selectors` UDP relay"."""
    src = inspect.getsource(master_node)
    assert "import selectors" in src
    assert "selectors.DefaultSelector" in src


# ===========================================================================
# P-04.01-8 -- the relay is an intermediate process
# ===========================================================================
def test_the_relay_is_intermediate(udp_sink):
    """P-04.01-8. [a: D3] "The relay is an intermediate process: SEM and PCS
    never communicate directly." With the relay stopped, a datagram the PCS
    sends does not reach the SEM's port.
    """
    sink = udp_sink(D4_ORCH_TO_SEM)
    send(b"should-not-arrive", D4_PCS_TO_ORCH)
    time.sleep(0.3)
    assert sink.datagrams == [], (
        "a datagram reached the SEM with no relay running -- the two are "
        "talking directly"
    )


def test_pcs_and_sem_send_and_receive_on_different_ports():
    """P-04.01-8. The four ports are distinct, so neither end can be listening on
    the port the other sends to. [a: D4]
    """
    four = {D4_PCS_TO_ORCH, D4_ORCH_TO_SEM, D4_SEM_TO_ORCH, D4_ORCH_TO_PCS}
    assert len(four) == 4
    assert ports.PCS_TO_ORCH != ports.ORCH_TO_SEM
    assert ports.SEM_TO_ORCH != ports.ORCH_TO_PCS


# ===========================================================================
# P-04.01-9 -- the two internal nodes, per the TA
# ===========================================================================
def test_two_internal_nodes_named_per_the_ta(relay):
    """P-04.01-9. [a: fsim_TA_Orch.sysml] the orchestrator owns `seNode` and
    `pcsNode`, each with the port pair the TA gives it. [a: D3] "two internal
    nodes per the TA".
    """
    assert hasattr(relay, "seNode")
    assert hasattr(relay, "pcsNode")
    assert {n.name for n in relay.nodes} == {"seNode", "pcsNode"}

    # [a: fsim_TA_Orch.sysml]
    #   seNode  { e_ownshipStateOut 5007; e_radarDetectionsIn 6007 }
    assert relay.seNode.outbound_port == 5007
    assert relay.seNode.inbound_port == 6007
    #   pcsNode { e_radarDetectionsOut 6006; e_ownshipStateIn 5006 }
    assert relay.pcsNode.outbound_port == 6006
    assert relay.pcsNode.inbound_port == 5006


def test_node_counters_attribute_traffic_to_the_right_node(relay, udp_sink):
    """P-04.01-9. Traffic from the PCS is received by pcsNode and forwarded to
    seNode's outbound port -- the TA's `pcsNode.i_Pcs2SeOut -> seNode.i_Pcs2SeIn`
    connection. [a: fsim_TA_Orch.sysml]
    """
    sink = udp_sink(D4_ORCH_TO_SEM)
    before = relay.pcsNode.stats.received
    send(b"probe", D4_PCS_TO_ORCH)
    assert sink.wait_for(1)
    assert relay.pcsNode.stats.received == before + 1
    assert relay.seNode.stats.received == 0


def test_a_real_ownship_state_survives_the_relay(relay, udp_sink):
    """P-04.01-1. End to end with a real message rather than a probe: what
    arrives at the SEM decodes to the state the PCS sent. [a: D1] [a: D19]
    """
    from test_common_messages import a_state

    sink = udp_sink(D4_ORCH_TO_SEM)
    original = a_state(seq=42)
    send(messages.encode(original), D4_PCS_TO_ORCH)
    assert sink.wait_for(1)
    assert messages.decode(sink.datagrams[0]) == original
