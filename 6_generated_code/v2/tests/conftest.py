"""Suite-wide fixtures. Satisfies D15 (test conventions) and D18 (hermeticity).

[a: D15] "pytest. Offscreen Qt (QT_QPA_PLATFORM=offscreen). Tests bind the real
UDP ports; the 'close running instances' caveat is documented. Long end-to-end
and convergence tests are marked slow. Every spawned thread is stoppable
(stop_event). Requirement ids are cited in test docstrings, or in the module
docstring where a module verifies one requirement throughout. The runner
invokes the environment's Python by path and pauses on exit. The suite runs in
.venv_fsim."

[a: D18] "No test depends on the machine it runs on. Tests do not read the real
PATH, do not touch the filesystem outside tmp_path, and do not spawn a real
external process. Every path the application writes to by default is a
parameter the suite can redirect. Deployment rows pass identically with and
without FlightGear installed."

CAVEAT, documented per D15: rows that bind 5005, 5006, 5007, 6006, 6007 or 5550
will fail if another instance of the application is already running on this
machine. Close running instances before running the suite. README.md and
run_tests.bat both say so.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

# Qt must be offscreen before anything imports it. [a: D15]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Qt writes a runtime dir warning without this; harmless but noisy, and it must
# not land outside tmp. Set only if the host has not. [c]
os.environ.setdefault("XDG_RUNTIME_DIR", os.environ.get("TMPDIR", "/tmp"))

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flightsim.common import ports  # noqa: E402


# ---------------------------------------------------------------------------
# Controlled clock -- so a rate is measured against a clock the test owns,
# never against the speed of the machine. [a: D18]
# ---------------------------------------------------------------------------
class FakeClock:
    """A monotonic clock the test advances by hand.

    Rates verified with this are verified by *counting events per unit of the
    clock*, which is what R-2 asks for, without making the row depend on how
    fast the host is. [b: D18]
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)
        self._lock = threading.Lock()

    def time(self) -> float:
        with self._lock:
            return self._t

    monotonic = time

    def advance(self, dt: float) -> float:
        with self._lock:
            self._t += float(dt)
            return self._t

    def sleep(self, dt: float) -> None:
        """A sleep that advances the clock instead of blocking."""
        self.advance(dt)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# ---------------------------------------------------------------------------
# UDP helpers
# ---------------------------------------------------------------------------
class UdpSink:
    """A bound UDP socket that collects datagrams on a background thread.

    Stoppable via `stop_event`, per D15.
    """

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self.datagrams: list[bytes] = []
        self.stop_event = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.settimeout(0.05)
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"udp-sink-{port}")
        self._thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                data, _ = self._sock.recvfrom(ports.MAX_DATAGRAM)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            self.datagrams.append(data)

    def wait_for(self, count: int = 1, timeout: float = 3.0) -> bool:
        """Wall-clock wait for datagrams to arrive.

        This is real time, not the FakeClock: it is waiting for the operating
        system's network stack, which no injected clock controls. It is a
        *bound*, never a measurement -- no pass criterion is derived from how
        long this took. [b: D18]
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.datagrams) >= count:
                return True
            time.sleep(0.005)
        return len(self.datagrams) >= count

    def close(self) -> None:
        self.stop_event.set()
        self._thread.join(timeout=2.0)
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def udp_sink():
    """Factory for bound UDP sinks; every sink is closed at teardown."""
    made: list[UdpSink] = []

    def _make(port: int, host: str = "127.0.0.1") -> UdpSink:
        sink = UdpSink(port, host)
        made.append(sink)
        return sink

    yield _make
    for sink in made:
        sink.close()


@pytest.fixture
def udp_send():
    """Send one datagram to a local port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(data: bytes, port: int, host: str = "127.0.0.1") -> None:
        sock.sendto(data, (host, port))

    yield _send
    sock.close()


def free_port() -> int:
    """An ephemeral port the OS says is free.

    For rows that need *a* port rather than *the* port -- a loopback tile stub,
    say. Rows that verify D4 use the real numbers. [b]
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Environment isolation [a: D18]
# ---------------------------------------------------------------------------
@pytest.fixture
def clean_env(monkeypatch):
    """An environment mapping with every FSIM_FG_* variable removed.

    Returned as a plain dict the test passes explicitly, and also cleared out of
    os.environ so that a code path which forgot to take its environment as a
    parameter is caught rather than accidentally passing. [a: D18]
    """
    from flightsim.common import config

    for name in config.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return {}


@pytest.fixture
def fake_flightgear_tree(tmp_path):
    """A FlightGear-shaped installation inside tmp_path.

    Used by the H-2 hermeticity row, which runs the deployment-sensitive rows
    once without this and once with it and requires identical results.
    [a: D18, R-2]

    No version number appears in the directory name this creates beyond the one
    the test supplies, and none is pinned. [a: Sec.5.3]
    """

    def _make(dirname: str = "FlightGear 1.0.0", with_aircraft: bool = True):
        root = tmp_path / "pf" / dirname
        (root / "bin").mkdir(parents=True)
        exe = root / "bin" / "fgfs.exe"
        exe.write_text("not a real executable", encoding="utf-8")
        aircraft_dir = None
        if with_aircraft:
            from flightsim.common import config

            aircraft_dir = tmp_path / "Aircraft"
            plane = aircraft_dir / config.AIRCRAFT_NAME
            plane.mkdir(parents=True)
            (plane / config.AIRCRAFT_SET_FILE).write_text("<PropertyList/>",
                                                          encoding="utf-8")
        return {
            "program_files_root": str(tmp_path / "pf"),
            "executable": str(exe),
            "install_root": str(root),
            "aircraft_dir": str(aircraft_dir) if aircraft_dir else None,
        }

    return _make


# ---------------------------------------------------------------------------
# Output hygiene [a: Sec.3 criterion 7, D18]
# ---------------------------------------------------------------------------
_IGNORED_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".git", ".venv_fsim", ".venv_hotas",
    "tmp", ".mypy_cache",
}


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """Path -> (size, mtime_ns) for every tracked file under `root`.

    Caches and virtual environments are excluded: pytest and CPython write to
    them by design, and neither is part of the working tree G9 inspects. [b]
    """
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIR_NAMES]
        for fn in filenames:
            if fn.endswith((".pyc", ".pyo")):
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture(scope="session")
def tree_snapshot():
    """Snapshot of the working tree taken once, at session start.

    `test_hermeticity.py::test_suite_writes_nothing_outside_tmp_path` compares
    against it. Making it session-scoped and requesting it there means the
    snapshot is taken before the first application module writes anything.
    """
    return _snapshot_tree(REPO_ROOT)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


# ---------------------------------------------------------------------------
# Qt
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the session, offscreen. [a: D15]

    Qt permits only one QApplication per process, so this is session-scoped;
    widgets are constructed and destroyed per test.
    """
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def qt_widgets(qapp):
    """Track widgets and close them at teardown, so no window outlives a row."""
    made = []

    def _track(widget):
        made.append(widget)
        return widget

    yield _track
    for w in reversed(made):
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:
            pass
    qapp.processEvents()


# ---------------------------------------------------------------------------
# Thread hygiene [a: D15] "Every spawned thread is stoppable (stop_event)."
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_leaked_threads():
    """Fail a row that leaves a non-daemon thread of ours running."""
    before = {t.ident for t in threading.enumerate()}
    yield
    time.sleep(0.02)
    leaked = [
        t for t in threading.enumerate()
        if t.ident not in before and t.is_alive() and not t.daemon
    ]
    assert not leaked, f"row leaked non-daemon thread(s): {[t.name for t in leaked]}"
