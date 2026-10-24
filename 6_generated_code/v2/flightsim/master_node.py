"""BusOrchestrator -- the network middleware. [a: fsim_TA_Orch.sysml] [a: D3]

[a: L2SYS-REQ-04.01] "The BusManager shall orchestrate the exchange of state
variables between the tactical databus and external UDP ports without blocking
the Python execution threads."

[a: D3] "Self-contained Python `selectors` UDP relay, no external middleware;
two internal nodes per the TA (seNode, pcsNode); payload-opaque; non-blocking;
stoppable on Windows. The relay is an intermediate process: SEM and PCS never
communicate directly."

The two nodes are the TA's own structure [a: fsim_TA_Orch.sysml]:

    seNode   e_ownshipStateOut  5007  ->  SEM
             e_radarDetectionsIn 6007 <-  SEM
             i_Pcs2SeIn, i_Se2PcsOut       internal peer ports

    pcsNode  e_radarDetectionsOut 6006 ->  PCS
             e_ownshipStateIn     5006 <-  PCS
             i_Pcs2SeOut, i_Se2PcsIn       internal peer ports

    connect seNode.i_Se2PcsOut  to pcsNode.i_Se2PcsIn
    connect pcsNode.i_Pcs2SeOut to seNode.i_Pcs2SeIn

so a datagram arriving on 5006 crosses `pcsNode -> seNode` and leaves on 5007,
and one arriving on 6007 crosses `seNode -> pcsNode` and leaves on 6006.

**Standard library only.** `test_orchestrator.py::
test_module_imports_only_the_standard_library` parses this file's imports and
asserts it. That is what "no external middleware" is checked against.
"""

from __future__ import annotations

import selectors
import socket
import threading
from dataclasses import dataclass, field
from typing import Callable

from flightsim.common import ports


@dataclass
class NodeStats:
    """Per-node counters, for the message log and for the suite. [b]"""

    received: int = 0
    forwarded: int = 0
    send_failures: int = 0


@dataclass
class Node:
    """One of the TA's two internal nodes. [a: fsim_TA_Orch.sysml]

    `inbound_port` is the port this node binds; `peer_out_host`/`peer_out_port`
    is where the peer node sends what this node hands it across the internal
    connection.
    """

    name: str
    inbound_port: int
    outbound_port: int
    stats: NodeStats = field(default_factory=NodeStats)
    socket: socket.socket | None = None


class BusOrchestrator:
    """The relay. [a: D3] [a: L2SYS-REQ-04.01]

    Non-blocking throughout: every socket is put in non-blocking mode and the
    loop waits in `selectors.DefaultSelector.select(timeout)`, which never
    holds the GIL waiting on a socket. [a: D3] "without blocking the Python
    execution threads" [a: L2SYS-REQ-04.01]

    Stoppable on Windows [a: D3]: `stop()` sets an event and the select loop
    wakes on its own timeout. No self-pipe and no socket shutdown is needed,
    both of which behave differently on Windows.
    """

    #: How long `select` waits before re-checking the stop event. Short enough
    #: that `stop()` returns promptly, long enough not to spin. [c] -- what
    #: would settle it: a stated shutdown-latency requirement.
    SELECT_TIMEOUT_S = 0.05

    def __init__(
        self,
        host: str = ports.LOOPBACK,
        bind_address: str = ports.BIND_ADDRESS,
        pcs_in_port: int = ports.PCS_TO_ORCH,
        sem_out_port: int = ports.ORCH_TO_SEM,
        sem_in_port: int = ports.SEM_TO_ORCH,
        pcs_out_port: int = ports.ORCH_TO_PCS,
        on_message: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.bind_address = bind_address
        self._on_message = on_message

        # [a: fsim_TA_Orch.sysml] the two nodes, with the TA's own port pairing
        self.pcsNode = Node("pcsNode", inbound_port=pcs_in_port,
                            outbound_port=pcs_out_port)
        self.seNode = Node("seNode", inbound_port=sem_in_port,
                           outbound_port=sem_out_port)

        self._selector: selectors.BaseSelector | None = None
        self._tx: socket.socket | None = None
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    # ------------------------------------------------------------------
    @property
    def nodes(self) -> tuple[Node, Node]:
        return (self.seNode, self.pcsNode)

    def _log(self, text: str) -> None:
        if self._on_message:
            self._on_message(text)

    def _bind(self, node: Node) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setblocking(False)          # [a: D3] non-blocking
        s.bind((self.bind_address, node.inbound_port))
        node.socket = s

    def open(self) -> None:
        """Bind both node sockets and the shared transmit socket."""
        if self._selector is not None:
            return
        self._selector = selectors.DefaultSelector()
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tx.setblocking(False)   # [a: D3] non-blocking
        for node in self.nodes:
            self._bind(node)
            self._selector.register(node.socket, selectors.EVENT_READ, node)

    def close(self) -> None:
        if self._selector is not None:
            for node in self.nodes:
                if node.socket is not None:
                    try:
                        self._selector.unregister(node.socket)
                    except (KeyError, ValueError):
                        pass
                    try:
                        node.socket.close()
                    except OSError:
                        pass
                    node.socket = None
            self._selector.close()
            self._selector = None
        if self._tx is not None:
            try:
                self._tx.close()
            except OSError:
                pass
            self._tx = None

    # ------------------------------------------------------------------
    def _relay_once(self, timeout: float | None = None) -> int:
        """One selector pass. Returns the number of datagrams forwarded.

        The forwarding is **payload-opaque** [a: D3]: the bytes are read and
        written unexamined. Nothing here decodes a message, so an unparseable
        or unknown payload crosses the bus unchanged, exactly as a valid one
        does.
        """
        assert self._selector is not None
        forwarded = 0
        events = self._selector.select(
            self.SELECT_TIMEOUT_S if timeout is None else timeout
        )
        for key, _mask in events:
            node: Node = key.data
            sock: socket.socket = key.fileobj  # type: ignore[assignment]
            while True:
                try:
                    data, _addr = sock.recvfrom(ports.MAX_DATAGRAM)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    break
                node.stats.received += 1
                # the internal peer connection: pcsNode -> seNode is
                # i_Pcs2SeOut -> i_Pcs2SeIn, and seNode -> pcsNode is
                # i_Se2PcsOut -> i_Se2PcsIn. [a: fsim_TA_Orch.sysml]
                peer = self.seNode if node is self.pcsNode else self.pcsNode
                if self._forward(peer, data):
                    node.stats.forwarded += 1
                    forwarded += 1
        return forwarded

    def _forward(self, peer: Node, data: bytes) -> bool:
        assert self._tx is not None
        try:
            self._tx.sendto(data, (self.host, peer.outbound_port))
            return True
        except OSError:
            peer.stats.send_failures += 1
            return False

    def run(self, stop_event: threading.Event | None = None) -> None:
        """The relay loop. [a: D3] [a: L2SYS-REQ-04.01]"""
        stop = stop_event or self.stop_event
        self.open()
        self._started.set()
        self._log(
            f"BusOrchestrator relaying "
            f"{self.pcsNode.inbound_port}->{self.seNode.outbound_port} and "
            f"{self.seNode.inbound_port}->{self.pcsNode.outbound_port}"
        )
        try:
            while not stop.is_set():
                self._relay_once()
        finally:
            self.close()
            self._started.clear()

    def start(self) -> threading.Thread:
        """Run the loop on a background thread. [a: D15] stoppable."""
        self.stop_event.clear()
        self._thread = threading.Thread(target=self.run, name="bus-orchestrator",
                                        daemon=True)
        self._thread.start()
        self._started.wait(timeout=2.0)
        return self._thread

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the loop and join. Safe on Windows. [a: D3]

        The loop wakes on its own `select` timeout, so no socket is closed from
        another thread and no self-pipe is needed -- both of which are the ways
        this usually breaks on Windows.
        """
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def __enter__(self) -> "BusOrchestrator":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m flightsim.master_node",
        description="Bus orchestrator. [a: L2SYS-REQ-04.01] [a: D3]",
    )
    p.add_argument("--host", default=ports.LOOPBACK)
    args = p.parse_args(argv)

    print("===========================================")
    print(" Bus Orchestrator (master_node)")
    print(f"   PCS  {ports.PCS_TO_ORCH} -> SEM  {ports.ORCH_TO_SEM}")
    print(f"   SEM  {ports.SEM_TO_ORCH} -> PCS  {ports.ORCH_TO_PCS}")
    print("===========================================")
    orch = BusOrchestrator(host=args.host, on_message=print)
    try:
        orch.run()
    except KeyboardInterrupt:
        print("\nShutting down the orchestrator.")
        orch.stop_event.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
