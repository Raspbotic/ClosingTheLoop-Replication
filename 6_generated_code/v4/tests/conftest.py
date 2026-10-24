"""Suite configuration. [a: Conv] offscreen Qt, real ports, stoppable threads.

CAVEAT: tests bind the real federation ports (5005, 5006, 5007, 6006, 6007, 5550).
Close any running flight simulator instances before running the suite.
"""
import os
import socket
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running end-to-end or autopilot test")


@pytest.fixture(scope="session")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["fsim-tests"])
    yield app


class UdpReceiver:
    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.05)

    def collect(self, duration):
        out, end = [], time.monotonic() + duration
        while time.monotonic() < end:
            try:
                out.append(self.sock.recv(65535))
            except socket.timeout:
                pass
        return out

    def close(self):
        self.sock.close()


@pytest.fixture
def udp_receiver():
    made = []

    def factory(port):
        r = UdpReceiver(port)
        made.append(r)
        return r
    yield factory
    for r in made:
        r.close()


def send_udp(port, data):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(data, ("127.0.0.1", port))
    s.close()


@pytest.fixture(autouse=True)
def no_leaked_threads():
    """[a: Conv] every spawned thread stoppable and joined in teardown."""
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        extra = [t for t in threading.enumerate() if t not in before and t.is_alive() and not t.daemon]
        if not extra:
            return
        time.sleep(0.05)
    pytest.fail(f"leaked non-daemon threads: {[t.name for t in extra]}")
