"""Integration rows (one per TA subsystem) and fit rows. Each starts the application object.
[a: fsim_TA_System.sysml#FlightSimSystem]"""
import threading
import time

import pytest
from PyQt5 import QtWidgets

from flightsim.common import ports
from flightsim.orch.master_node import BusOrchestrator
from flightsim.sem.run_sem import SemApp
from flightsim.hotas.hotas_standalone import HotasTransmitter, DemoSource

AVAIL_W, AVAIL_H = 1280, 650  # 1280x720 logical minus title bar and taskbar (plan section 4)


def pump(pred, timeout=3.0):
    app = QtWidgets.QApplication.instance()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def pcs(qapp, tmp_path):
    from flightsim.pcs.app import PilotControlStation
    def offline(url):
        raise OSError("offline (test)")
    p = PilotControlStation(data_dir=str(tmp_path), tile_fetch=offline,
                            env={"FSIM_FGFS_EXE": str(tmp_path / "missing")})
    yield p
    p.shutdown()


def test_pcs_window_shown(pcs):
    """L2SYS-REQ-01.08: the application creates and shows the virtual cockpit console (PCS)."""
    assert pcs.ui.isVisible() and pcs.ui.windowTitle() == "F-16 PCS (Pilot Control Station)"


def test_pcs_parts_wired(pcs):
    """INT-PCS: uiManager, ioManager, threadPhysics, threadMapWindow present; physics telemetry
    reaches the UI (i_OwnTelemetryOut -> i_OwnTelemetryIn); UI commands reach physics."""
    assert pcs.ui and pcs.io and pcs.physics._thread.is_alive() and pcs.map_window
    pcs.ui.ssf_buttons["run"].click()
    assert pump(lambda: pcs.telemetry["running"] and pcs.telemetry["sim_time_s"] > 0.3)
    assert pcs.ui.lbl_state.text() == "SIM: RUNNING"


def test_map_window_created_by_app(pcs):
    """INT-MAP / L2SYS-REQ-02.01: the application creates and shows the tactical map window,
    which receives ownship state (i_OwnStateOut -> i_OwnStateIn)."""
    assert pcs.map_window.isVisible() and pcs.map_window.windowTitle() == "PCS - Tactical GPS Map (Live)"
    assert pump(lambda: pcs.map_window.mapGuiWorker.own is not None)


def test_orchestrator_nodes():
    """INT-ORCH: seNode and pcsNode bound to the TA ports."""
    o = BusOrchestrator()
    try:
        assert o.pcsNode.in_port == 5006 and o.pcsNode.out_port == 6006
        assert o.seNode.in_port == 6007 and o.seNode.out_port == 5007
        assert o.pcsNode.peer is o.seNode and o.seNode.peer is o.pcsNode
    finally:
        o.pcsNode.close(); o.seNode.close()


def test_sem_window_shown(qapp):
    """L2SYS-REQ-03.01: the Synthetic Environment application creates its window."""
    s = SemApp(show=True)
    try:
        assert s.window.isVisible() and s.window.windowTitle() == "SEM (Synthetic Environment Manager)"
    finally:
        s.shutdown()


def test_sem_receives_ownship(pcs):
    """INT-SEM: SEM receives the ownship relayed by the orchestrator (L2 -> L3)."""
    o = BusOrchestrator().start()
    s = SemApp(show=True)
    s.window.btn_connect.click()
    try:
        assert pump(lambda: (s.window.tick() or True) and s.core.status() == "Connected", 3.0)
        assert s.core.ownship.lat_deg == pytest.approx(pcs.telemetry["lat_deg"], abs=1e-4)
    finally:
        s.shutdown(); o.stop()


def test_hotas_reaches_pcs(pcs):
    """INT-HOTAS: demo HOTAS datagrams (L1) claim control in the PCS."""
    tx = HotasTransmitter(DemoSource())
    stop = threading.Event()
    th = threading.Thread(target=tx.run, args=(stop,), name="hotas-demo")
    th.start()
    try:
        assert pump(lambda: pcs.ui.vhotas.locked and pcs.ui.lbl_hotas.text() == "HOTAS: PHYSICAL")
    finally:
        stop.set(); th.join(2)


def _fits(w):
    w.adjustSize()
    mh = w.minimumSizeHint()
    return mh.width() <= AVAIL_W and mh.height() <= AVAIL_H, mh


def test_fit_pcs(pcs):
    """FIT-PCS: minimum size within 1280x650 logical."""
    ok, mh = _fits(pcs.ui)
    assert ok, mh


def test_fit_map(pcs):
    """FIT-MAP."""
    ok, mh = _fits(pcs.map_window)
    assert ok, mh


def test_fit_sem(qapp):
    """FIT-SEM."""
    s = SemApp(show=True)
    try:
        ok, mh = _fits(s.window)
        assert ok, mh
    finally:
        s.shutdown()
