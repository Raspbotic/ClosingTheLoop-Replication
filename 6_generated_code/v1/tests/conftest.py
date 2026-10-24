"""
tests/conftest.py
=================
Shared pytest fixtures for the regression suite.

TRACEABILITY
------------
[a: Decision D15] "pytest; offscreen Qt (QT_QPA_PLATFORM=offscreen); tests
    bind the real UDP ports (document the 'close running instances'
    caveat); long-running end-to-end and autopilot tests marked slow;
    every spawned thread stoppable (stop_event) so no test leaks input
    into later tests; requirement IDs cited in test docstrings."

CAVEAT (D15): the suite binds the REAL federation ports of D4. Close any
running FlightSim process (PCS, orchestrator, SEM, HOTAS) before running
the tests, or the binds will fail with EADDRINUSE.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

# [a: D15] offscreen Qt must be set before any Qt import anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# [a: D15] the runner invokes the venv python by path; make the package
# importable from either the repository root or tests/.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: long-running end-to-end / autopilot / real-time test [a: D15]")


# --------------------------------------------------------------------------
# UDP helpers
# --------------------------------------------------------------------------

class UdpReceiver:
    """Bind a real UDP port and collect datagrams on a stoppable thread.

    [a: D15] every spawned thread is stoppable so no test leaks input into
    a later test.
    """

    def __init__(self, port: int, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.datagrams: list[bytes] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.settimeout(0.05)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"udp-rx-{port}")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            self.datagrams.append(data)

    def wait_for(self, count: int = 1, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.datagrams) >= count:
                return True
            time.sleep(0.01)
        return len(self.datagrams) >= count

    def clear(self) -> None:
        self.datagrams.clear()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        try:
            self._sock.close()
        except OSError:                       # [a: D3] teardown race tolerance
            pass


@pytest.fixture
def udp_receiver():
    """Factory fixture: udp_receiver(port) -> UdpReceiver, auto-closed."""
    made: list[UdpReceiver] = []

    def _make(port: int, host: str = "127.0.0.1") -> UdpReceiver:
        rx = UdpReceiver(port, host)
        made.append(rx)
        return rx

    yield _make
    for rx in made:
        rx.close()


@pytest.fixture
def udp_sender():
    """A plain UDP sending socket, closed at teardown."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yield sock
    sock.close()


# --------------------------------------------------------------------------
# Qt
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    """One offscreen QApplication for the whole session. [a: D15]"""
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


# --------------------------------------------------------------------------
# Thread hygiene guard
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_thread_leak():
    """[a: D15] fail loudly if a test leaves one of our threads running."""
    before = {t.name for t in threading.enumerate()}
    yield
    time.sleep(0.05)
    after = {t.name for t in threading.enumerate()}
    leaked = {n for n in (after - before)
              if n.startswith(("physics", "orch", "hotas", "udp-rx",
                               "sem-", "tile-"))}
    assert not leaked, f"threads leaked into later tests [a: D15]: {leaked}"
