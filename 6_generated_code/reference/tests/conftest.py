"""Regression suite config: offscreen Qt, project-root imports, shared
fixtures. Run from project root:  run_tests.bat  (or: pytest -v tests/)"""
import os, sys, socket, pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def process_events(qapp, seconds: float):
    """Pump the Qt event loop for a wall-time duration."""
    from PyQt5.QtCore import QTimer, QEventLoop
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec_()


@pytest.fixture
def udp_rx():
    """Factory for bound, non-blocking UDP receive sockets w/ cleanup."""
    socks = []
    def make(port, timeout=None):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", port))
        if timeout is None:
            s.setblocking(False)
        else:
            s.settimeout(timeout)
        socks.append(s)
        return s
    yield make
    for s in socks:
        s.close()
