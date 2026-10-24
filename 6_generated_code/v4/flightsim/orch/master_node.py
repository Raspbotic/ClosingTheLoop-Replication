"""BusOrchestrator — BusManager. [a: fsim_TA_Orch.sysml#BusOrchestrator] (master_node.py)

[a: L2SYS-REQ-04.01] Exchanges state variables between the tactical databus and the
external UDP ports without blocking the Python execution threads.
[a: Behaviour B3] Standard-library UDP relay, no middleware, one internal node per TA node,
payload-opaque (never decodes), non-blocking selectors loop, OSError-tolerant shutdown.

Usage:  python -m flightsim.orch.master_node
"""
from __future__ import annotations

import os
import selectors
import socket
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flightsim.common import ports


class _Node:
    """One TA node: owns one receive socket (inbound boundary port) and sends on the
    outbound boundary port. Internal ports i_* are plain method calls (relay())."""

    def __init__(self, name, in_port, out_port, host=ports.LOOPBACK):
        self.name = name
        self.in_port, self.out_port = in_port, out_port
        self.out_addr = (host, out_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.sock.bind((host, in_port))
        self.sock.setblocking(False)  # [a: L2SYS-REQ-04.01] non-blocking
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tx.setblocking(False)
        self.peer = None
        self.rx_count = 0
        self.tx_count = 0

    def emit(self, payload: bytes):
        """Transmit on the outbound boundary port; payload untouched. [a: Behaviour B3]"""
        try:
            self.tx.sendto(payload, self.out_addr)
            self.tx_count += 1
        except (BlockingIOError, OSError):
            pass  # a lost datagram never blocks the relay

    def drain(self):
        while True:
            try:
                payload = self.sock.recv(65535)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return  # Windows: ICMP port-unreachable surfaces as ConnectionResetError
            self.rx_count += 1
            self.peer.emit(payload)  # i_Pcs2SeOut -> i_Pcs2SeIn / i_Se2PcsOut -> i_Se2PcsIn

    def close(self):
        for s in (self.sock, self.tx):
            try:
                s.close()
            except OSError:
                pass  # Windows teardown race [a: Behaviour B3]


class BusOrchestrator:
    def __init__(self, host=ports.LOOPBACK):
        # pcsNode: e_ownshipStateIn 5006, e_radarDetectionsOut 6006 [a: fsim_TA_Orch.sysml#pcsNode]
        self.pcsNode = _Node("pcsNode", ports.PCS_OWNSHIP_OUT_PORT, ports.PCS_RADAR_IN_PORT, host)
        # seNode: e_radarDetectionsIn 6007, e_ownshipStateOut 5007 [a: fsim_TA_Orch.sysml#seNode]
        self.seNode = _Node("seNode", ports.SEM_RADAR_OUT_PORT, ports.SEM_OWNSHIP_IN_PORT, host)
        # connect pcsNode.i_Pcs2SeOut to seNode.i_Pcs2SeIn; seNode.i_Se2PcsOut to pcsNode.i_Se2PcsIn
        self.pcsNode.peer = self.seNode
        self.seNode.peer = self.pcsNode
        self._stop = threading.Event()
        self._thread = None

    def run(self, stop_event=None):
        """busRelay: selectors loop; wakes on its own timeout and closes its own sockets."""
        stop = stop_event or self._stop
        sel = selectors.DefaultSelector()
        for n in (self.pcsNode, self.seNode):
            sel.register(n.sock, selectors.EVENT_READ, n)
        try:
            while not stop.is_set():
                for key, _ in sel.select(timeout=0.1):
                    key.data.drain()
        finally:
            try:
                sel.close()
            except OSError:
                pass
            self.pcsNode.close()
            self.seNode.close()

    def start(self):
        self._thread = threading.Thread(target=self.run, name="BusOrchestrator")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2.0)
            self._thread = None


def main():
    print("=====================================================")
    print(f" Bus Orchestrator: PCS {ports.PCS_OWNSHIP_OUT_PORT}->{ports.SEM_OWNSHIP_IN_PORT} SEM,"
          f" SEM {ports.SEM_RADAR_OUT_PORT}->{ports.PCS_RADAR_IN_PORT} PCS")
    print("=====================================================")
    try:
        orch = BusOrchestrator()
    except OSError as e:
        print(f"ERROR: cannot bind orchestrator ports ({e}). Close running instances and retry.")
        return 1
    try:
        orch.run()
    except KeyboardInterrupt:
        orch._stop.set()
    print("Orchestrator stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
