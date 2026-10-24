"""Helpers: the PCS application under test, stepped deterministically.
The physics object is the application's own (obtained from the app); its real-time thread
is paused and the same step() is driven by the test clock."""
import time
from PyQt5 import QtWidgets


class FakeClock:
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t


def make_pcs(tmp_path, clock=None, fetch=None, network=True):
    from flightsim.pcs.app import PilotControlStation
    def offline(url):
        raise OSError("offline (test)")
    pcs = PilotControlStation(data_dir=str(tmp_path), start_network=network, tile_fetch=fetch or offline,
                              clock=clock or FakeClock(), env={"FSIM_FGFS_EXE": str(tmp_path / "missing-fgfs")})
    pcs.physics.stop_thread()
    pcs.io.terrain.stop()
    return pcs


def advance(pcs, seconds, clock=None):
    """Advance simulated time; UI and I/O ticks at their own rates on the same clock."""
    app = QtWidgets.QApplication.instance()
    n = int(round(seconds * 120))
    for i in range(n):
        pcs.physics.step()
        if clock is not None:
            clock.t += 1.0 / 120
        if i % 4 == 0:
            pcs.io_tick()
        if i % 4 == 0:
            pcs.ui_tick()
            app.processEvents()
    pcs.io_tick(); pcs.ui_tick(); app.processEvents()
    return pcs.telemetry


def tick(pcs):
    pcs.io_tick(); pcs.physics.step(); pcs.ui_tick()
    QtWidgets.QApplication.instance().processEvents()
    return pcs.telemetry
