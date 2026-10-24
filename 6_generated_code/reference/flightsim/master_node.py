"""
flightsim.master_node
=====================
BusOrchestrator: network middleware relaying between PCS and SEM.
Option A implementation (customer decision 2026-07-11): self-contained
Python UDP relay, no SEN framework dependency.

TRACEABILITY
------------
[a] Implements part def BusOrchestrator (fsim_TA_Orch.sysml)
    :> BusManager (fsim_LA.sysml). TA doc names this file:
    "master_node.py". Runtime: Python 3.14 main venv [a: TA attribute].
[a] Requirement L2SYS-REQ-04.01 (fsim_Design.xlsx): non-blocking bus
    management. Use case UC_DatabusManagement (subject = BusManager).
[a] Internal structure mirrors the TA parts 1:1:
      part seNode  -> class SeNode   (SEM-facing endpoint)
      part pcsNode -> class PcsNode  (PCS-facing endpoint)
      part senBus  -> class SenBus   (in-process message bus)
    and the TA internal connects:
      seNode.i_Se2PcsOut  -> pcsNode.i_Se2PcsIn   (radar path)
      pcsNode.i_Pcs2SeOut -> seNode.i_Pcs2SeIn    (ownship path)

PORT MAP [a: fsim_TA_Orch.sysml + HL architecture diagrams]
-----------------------------------------------------------------------
  pcsNode.e_ownshipStateIn     bind 5006    <- PCS ownship state
  pcsNode.e_radarDetectionsOut send to 6006 -> PCS radar detections
  seNode.e_radarDetectionsIn   bind 6007    <- SEM radar detections
  seNode.e_ownshipStateOut     send to 5007 -> SEM ownship state
  NOTE [b]: fsim_TA_SEM.sysml carries these two numbers swapped
  (erratum confirmed by customer 2026-07-11, diagrams + Orch file
  authoritative); all code uses the symbolic constants from
  common/ports.py, which implement the confirmed assignment.

DEVELOPER CHOICES [c]
---------------------
- selectors-based single-threaded event loop: satisfies "non-blocking"
  [a: L2SYS-REQ-04.01] with zero busy-wait and no thread coordination.
- Payloads are relayed OPAQUELY (no decode/re-encode): the orchestrator
  is middleware, not a message consumer; this keeps it schema-agnostic
  and minimizes added latency. Malformed datagrams are still counted.
- 5 s console statistics line for observability during integration.
"""

from __future__ import annotations

import argparse
import selectors
import socket
import time

from flightsim.common import ports


class SenBus:
    """part senBus [a]: in-process bus. With a single-threaded event loop
    the 'bus' is realized as direct handoff plus per-route counters --
    the TA connects are the two routes registered on it. [b]"""

    def __init__(self):
        self.routes: dict[str, dict] = {}

    def add_route(self, name: str, tx_sock: socket.socket,
                  dest: tuple[str, int]) -> None:
        self.routes[name] = {"sock": tx_sock, "dest": dest,
                             "count": 0, "bytes": 0}

    def forward(self, name: str, payload: bytes) -> None:
        r = self.routes[name]
        r["sock"].sendto(payload, r["dest"])
        r["count"] += 1
        r["bytes"] += len(payload)


class _Node:
    """Common endpoint behaviour for seNode / pcsNode [b]."""

    def __init__(self, name: str, bus: SenBus, sel: selectors.BaseSelector,
                 bind_port: int, route_out: str):
        self.name = name
        self._bus = bus
        self._route_out = route_out
        self.rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx_sock.bind((ports.LOCALHOST, bind_port))
        self.rx_sock.setblocking(False)                # [a: 04.1]
        sel.register(self.rx_sock, selectors.EVENT_READ, self.on_readable)
        self.tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def on_readable(self) -> None:
        try:
            while True:
                payload, _ = self.rx_sock.recvfrom(65535)
                self._bus.forward(self._route_out, payload)
        except BlockingIOError:
            pass
        except OSError:
            # [c] Windows raises WinError 10038 if the socket is closed
            # by stop() while the selector loop is mid-poll (teardown
            # race); benign -- the run loop is about to exit anyway.
            pass

    def close(self) -> None:
        self.rx_sock.close()
        self.tx_sock.close()


class BusOrchestrator:
    """part def BusOrchestrator [a] -- the process assembly."""

    def __init__(self):
        self.sel = selectors.DefaultSelector()
        self.bus = SenBus()                            # part senBus [a]

        # part pcsNode [a]: e_ownshipStateIn bind 5006,
        # forwards via i_Pcs2SeOut -> seNode -> SEM 6007
        self.pcs_node = _Node("pcsNode", self.bus, self.sel,
                              bind_port=ports.UDP_PCS_OWNSHIP_STATE,
                              route_out="pcs2se")
        # part seNode [a]: e_radarDetectionsIn bind 5007,
        # forwards via i_Se2PcsOut -> pcsNode -> PCS 6006
        self.se_node = _Node("seNode", self.bus, self.sel,
                             bind_port=ports.UDP_SEM_RADAR_DETECTIONS,
                             route_out="se2pcs")

        # TA internal connects [a] as bus routes:
        # pcsNode.i_Pcs2SeOut -> seNode.i_Pcs2SeIn -> e_ownshipStateOut
        self.bus.add_route("pcs2se", self.se_node.tx_sock,
                           (ports.LOCALHOST, ports.UDP_SEM_OWNSHIP_STATE))
        # seNode.i_Se2PcsOut -> pcsNode.i_Se2PcsIn -> e_radarDetectionsOut
        self.bus.add_route("se2pcs", self.pcs_node.tx_sock,
                           (ports.LOCALHOST, ports.UDP_PCS_RADAR_DETECTIONS))

        self._running = False

    def run(self, stats_period_s: float = 5.0) -> None:
        self._running = True
        print(f"[orch] BusOrchestrator up: "
              f"PCS ownship {ports.UDP_PCS_OWNSHIP_STATE} -> "
              f"SEM {ports.UDP_SEM_OWNSHIP_STATE} | "
              f"SEM radar {ports.UDP_SEM_RADAR_DETECTIONS} -> "
              f"PCS {ports.UDP_PCS_RADAR_DETECTIONS}")
        t_stats = time.monotonic() + stats_period_s
        try:
            while self._running:
                try:
                    events = self.sel.select(timeout=0.5)
                except OSError:
                    break        # [c] selector closed during stop()
                for key, _ in events:
                    key.data()                          # node.on_readable
                if stats_period_s and time.monotonic() >= t_stats:
                    t_stats += stats_period_s
                    r1 = self.bus.routes["pcs2se"]
                    r2 = self.bus.routes["se2pcs"]
                    print(f"[orch] pcs->se {r1['count']} msgs "
                          f"({r1['bytes']} B) | se->pcs {r2['count']} msgs "
                          f"({r2['bytes']} B)")
        except KeyboardInterrupt:
            print("\n[orch] stopped.")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self.se_node.close()
        self.pcs_node.close()
        self.sel.close()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        description="FlightSim Bus Orchestrator (TA: BusOrchestrator)")
    p.add_argument("--stats", type=float, default=5.0,
                   help="statistics print period in s, 0 = silent [c]")
    args = p.parse_args(argv)
    BusOrchestrator().run(stats_period_s=args.stats)


if __name__ == "__main__":
    main()