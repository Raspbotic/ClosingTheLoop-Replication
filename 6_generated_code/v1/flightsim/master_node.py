"""
flightsim/master_node.py
========================
Bus Orchestrator: the tactical databus middleware.

TRACEABILITY
------------
[a] Implements part def BusOrchestrator (fsim_TA_Orch.sysml), whose doc
    comment names this file: "Network middleware (master_node.py)".
    BusOrchestrator specializes FlightSimSystem::BusManager (fsim_LA.sysml).
[a] L2SYS-REQ-04.01: "The BusManager shall orchestrate the exchange of
    state variables between the tactical databus and external UDP ports
    without blocking the Python execution threads." (L2SYS-FCN-04.1,
    allocated to BusManager; supports UC_DatabusManagement.)
[a: Decision D3] "Orchestrator. Self-contained Python selectors UDP relay
    (no external middleware), two internal nodes per the TA (seNode,
    pcsNode), payload-opaque, non-blocking; clean OSError-tolerant
    shutdown (Windows teardown race)."
[a: Decision D4] port map, via flightsim.common.ports.
[a] Runtime: PythonRuntime version "3.14" (fsim_TA_Orch.sysml) ->
    .venv_fsim [a: D5].

TA STRUCTURE -> CODE MAPPING
---------------------------
    seNode.e_ownshipStateOut   (5007) -> _se_out_addr    (to the SEM)
    seNode.e_radarDetectionsIn (6007) -> _se_in_sock     (from the SEM)
    pcsNode.e_ownshipStateIn   (5006) -> _pcs_in_sock    (from the PCS)
    pcsNode.e_radarDetectionsOut(6006)-> _pcs_out_addr   (to the PCS)
    pcsNode.i_Pcs2SeOut -> seNode.i_Pcs2SeIn  -> _relay(pcs_in -> se_out)
    seNode.i_Se2PcsOut  -> pcsNode.i_Se2PcsIn -> _relay(se_in  -> pcs_out)
    busRelay -> the single selectors loop that carries both internal links

PAYLOAD OPACITY [a: D3]
-----------------------
The orchestrator never decodes a datagram. It forwards the received bytes
verbatim, so a change to the D1 wire format needs no change here and the
relay cannot corrupt a message it does not understand.
"""

from __future__ import annotations

import argparse
import selectors
import socket
import threading
import time
from typing import Optional

from flightsim.common import ports

#: [c] Maximum datagram size accepted in one recvfrom; larger than any D1
#: message and than the 408-byte FGNetFDM frame.
MAX_DATAGRAM = 65535


def _udp_socket(bind_port: Optional[int] = None,
                host: str = ports.LOCALHOST) -> socket.socket:
    """[a: D3] every socket is non-blocking, so no thread can block."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # [c] a generous receive buffer keeps a burst in the kernel rather than
    # dropping it while the selectors loop drains the other socket.
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    except OSError:
        pass
    if bind_port is not None:
        sock.bind((host, int(bind_port)))
    sock.setblocking(False)                       # [a: D3] non-blocking
    return sock


class BusOrchestrator:
    """part def BusOrchestrator (fsim_TA_Orch.sysml).

    Two internal nodes (seNode, pcsNode) whose internal signal ports are
    realized by the in-process forwarding of the single selectors loop
    (part busRelay).
    """

    def __init__(self, host: str = ports.LOCALHOST,
                 pcs_in_port: int = ports.UDP_PCS_TO_ORCH,
                 se_out_port: int = ports.UDP_ORCH_TO_SEM,
                 se_in_port: int = ports.UDP_SEM_TO_ORCH,
                 pcs_out_port: int = ports.UDP_ORCH_TO_PCS,
                 stop_event: Optional[threading.Event] = None):
        self.host = host
        # pcsNode.e_ownshipStateIn (5006) [a: D4]
        self._pcs_in_sock = _udp_socket(pcs_in_port, host)
        # seNode.e_radarDetectionsIn (6007) [a: D4]
        self._se_in_sock = _udp_socket(se_in_port, host)
        # seNode.e_ownshipStateOut (5007) / pcsNode.e_radarDetectionsOut (6006)
        self._out_sock = _udp_socket(None, host)
        self._se_out_addr = (host, int(se_out_port))
        self._pcs_out_addr = (host, int(pcs_out_port))

        self._sel = selectors.DefaultSelector()   # part busRelay [a: D3]
        # seNodeAction1 / pcsNodeAction1: relay the peer's traffic outward
        self._sel.register(self._pcs_in_sock, selectors.EVENT_READ,
                           ("pcs->se", self._se_out_addr))
        self._sel.register(self._se_in_sock, selectors.EVENT_READ,
                           ("se->pcs", self._pcs_out_addr))

        self.stop_event = stop_event or threading.Event()   # [a: D15]
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        # Diagnostics only; the relay itself stays payload-opaque [a: D3].
        self.counts = {"pcs->se": 0, "se->pcs": 0}

    # ------------------------------------------------------------------
    def _drain(self, sock: socket.socket, label: str, dest) -> None:
        """Forward every queued datagram byte-for-byte. [a: D3] opaque."""
        while True:
            try:
                data, _src = sock.recvfrom(MAX_DATAGRAM)
            except BlockingIOError:
                return
            except OSError:               # [a: D3] shutdown race tolerance
                return
            try:
                self._out_sock.sendto(data, dest)
            except OSError:
                return
            self.counts[label] += 1

    def poll(self, timeout: float = 0.05) -> int:
        """One pass of the selectors loop; returns datagrams forwarded.

        [a] L2SYS-REQ-04.01 "without blocking the Python execution
        threads": every socket is non-blocking and the loop waits only on
        the selector with a bounded timeout.
        """
        forwarded_before = sum(self.counts.values())
        try:
            events = self._sel.select(timeout=timeout)
        except OSError:                   # [a: D3] Windows teardown race
            return 0
        for key, _mask in events:
            label, dest = key.data
            self._drain(key.fileobj, label, dest)     # type: ignore[arg-type]
        return sum(self.counts.values()) - forwarded_before

    def run(self) -> None:
        """The relay loop. [a] seNodeAction1 / pcsNodeAction1."""
        print(f"[orch] relay up: "
              f"PCS {self._pcs_in_sock.getsockname()[1]} -> "
              f"SEM {self._se_out_addr[1]} | "
              f"SEM {self._se_in_sock.getsockname()[1]} -> "
              f"PCS {self._pcs_out_addr[1]}")
        while not self.stop_event.is_set():
            self.poll(timeout=0.05)

    def start(self) -> "BusOrchestrator":
        """Run the loop on its own thread. [a: D15] stoppable."""
        self._thread = threading.Thread(target=self.run, daemon=True,
                                        name="orch-relay")
        self._thread.start()
        return self

    def stop(self) -> None:
        """[a: D3] "clean OSError-tolerant shutdown (Windows teardown
        race)". Safe to call more than once."""
        self.stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._closed:
            return
        self._closed = True
        for sock in (self._pcs_in_sock, self._se_in_sock, self._out_sock):
            try:
                self._sel.unregister(sock)
            except (KeyError, ValueError, OSError):
                pass
            try:
                sock.close()
            except OSError:
                pass
        try:
            self._sel.close()
        except OSError:
            pass

    def __enter__(self) -> "BusOrchestrator":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        description="Flight Simulator bus orchestrator (TA: BusOrchestrator)")
    p.add_argument("--host", default=ports.LOCALHOST,
                   help="bind/forward host (default 127.0.0.1) [a: TA address]")
    p.add_argument("--stats", action="store_true",
                   help="print relay counters once per second [c]")
    args = p.parse_args(argv)

    orch = BusOrchestrator(host=args.host)
    try:
        if args.stats:
            orch.start()
            while True:
                time.sleep(1.0)
                print(f"\r[orch] pcs->se={orch.counts['pcs->se']} "
                      f"se->pcs={orch.counts['se->pcs']}   ",
                      end="", flush=True)
        else:
            orch.run()
    except KeyboardInterrupt:
        print("\n[orch] stopped.")
    finally:
        orch.stop()


if __name__ == "__main__":
    main()
