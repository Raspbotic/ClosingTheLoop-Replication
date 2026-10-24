"""E2E: every TA process in one process space over the real ports. [a: fsim_TA_System.sysml]
Realizes 02.03 reception across the real bus."""
import threading
import time

import pytest
from PyQt5 import QtWidgets

from flightsim.orch.master_node import BusOrchestrator
from flightsim.sem.run_sem import SemApp
from flightsim.hotas.hotas_standalone import HotasTransmitter, DemoSource


def pump(pred, timeout):
    app = QtWidgets.QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.slow
def test_full_federation(qapp, tmp_path):
    """L2SYS-REQ-02.03, L2SYS-REQ-04.01, L2SYS-REQ-03.05: ownship reaches SEM; demo controls reach
    PCS; an injected beacon reaches PCS and map with correct detection; a tombstone clears both."""
    from flightsim.pcs.app import PilotControlStation
    def offline(url):
        raise OSError("offline (test)")
    orch = BusOrchestrator().start()
    pcs = PilotControlStation(data_dir=str(tmp_path), tile_fetch=offline, env={"FSIM_FGFS_EXE": str(tmp_path / "x")})
    sem = SemApp(show=True)
    sem.window.btn_connect.click()
    tx = HotasTransmitter(DemoSource())
    stop = threading.Event()
    th = threading.Thread(target=tx.run, args=(stop,), name="hotas-demo")
    th.start()
    try:
        assert pump(lambda: sem.core.status() == "Connected", 3)                       # L2/L3
        assert pump(lambda: pcs.ui.vhotas.locked, 2)                                   # L1
        w = sem.window
        w.sp_range.setValue(20000); w.sp_theta.setValue(0); w.cmb_type.setCurrentText("EM_FOE"); w.btn_add.click()
        w.sp_range.setValue(80000); w.cmb_type.setCurrentText("EM_UNKNOWN"); w.btn_add.click()
        mg = pcs.map_window.mapGuiWorker
        assert pump(lambda: len(pcs.io.beacons) == 2 and len(mg.beacons) == 2, 3)      # L4/L5
        assert pump(lambda: pcs.io.beacons[1].detected and not pcs.io.beacons[2].detected, 3)
        assert 38000 < pcs.io.beacons[1].max_range_m < 40000
        w.sp_index.setValue(1); w.btn_delete.click()                                    # tombstone
        assert pump(lambda: 1 not in pcs.io.beacons and 1 not in mg.beacons, 3)
    finally:
        stop.set(); th.join(2)
        sem.shutdown(); pcs.shutdown(); orch.stop()
