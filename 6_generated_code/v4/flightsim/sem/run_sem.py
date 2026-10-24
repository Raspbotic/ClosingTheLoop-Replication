"""SyntheticEnvironmentMgr. [a: fsim_TA_SEM.sysml#SyntheticEnvironmentMgr] (run_sem.py)

threadMainGui actions: connectToSim, manageEwScenario (manageEwBeacon, controlOwnshipRcs),
updateSEUI, calcBeaconOwnshipRange, calcRadarEquation.
[a: L2SYS-REQ-03.01] [a: L2SYS-REQ-03.02] [a: L2SYS-REQ-03.03] [a: L2SYS-REQ-03.04]
[a: L2SYS-REQ-03.05] [a: Behaviour B2] [a: Behaviour B10]
Ports: e_ownshipStateIn UDP 5007, e_radarDetectionsOut UDP 6007.
Layout per Desired_UIUX_capture.JPG (SEM window); data per 03.03/03.04/B10 (finding F7).

Usage:  python -m flightsim.sem.run_sem
"""
from __future__ import annotations

import os
import socket
import sys
import time
from dataclasses import dataclass, field

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flightsim.common import ports
from flightsim.common import messages as msg
from flightsim.sem import radar
from flightsim.pcs.airfields import SEED, DEFAULT_ICAO

CONNECT_TIMEOUT_S = 3.0  # [c] status reverts to "Waiting" if no ownship for 3 s


@dataclass
class EwBeacon:
    beacon_id: int
    classification: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    use_radar_eq: bool = True            # [a: Behaviour B10] beacons without emitter params keep explicit range
    explicit_range_m: float = 20000.0    # [a: L2SYS-REQ-03.02] "defined maximum ranges"
    pt_w: float = radar.DEFAULT_PT_W
    g_dbi: float = radar.DEFAULT_G_DBI
    f_hz: float = radar.DEFAULT_F_HZ
    pmin_dbm: float = radar.DEFAULT_PMIN_DBM
    max_range_m: float = 0.0
    slant_range_m: float = -1.0
    detected: bool = False


class SemCore:
    """Scenario logic and federation I/O, GUI-free (driven by the GUI thread's timers)."""

    def __init__(self, clock=time.monotonic, host=ports.LOOPBACK):
        self.clock = clock
        self.host = host
        self.beacons: dict[int, EwBeacon] = {}
        self.next_id = 1
        self.rcs_m2 = radar.DEFAULT_RCS_M2  # [a: L2SYS-REQ-03.04] [a: Behaviour B10]
        self.ownship = None
        self.last_ownship_t = None
        self.rx = None
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tx_addr = (host, ports.SEM_RADAR_OUT_PORT)
        self.last_refresh = clock()
        self.sent = 0
        self.rejected = 0
        self.log = []

    # connectToSim
    @property
    def connected(self):
        return self.rx is not None

    def connect(self):
        """Join the federation: bind e_ownshipStateIn and publish every defined beacon.
        Nothing is received or transmitted before this. [a: fsim_TA_SEM.sysml#threadMainGui::connectToSim]"""
        if self.rx is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((self.host, ports.SEM_OWNSHIP_IN_PORT))
            s.setblocking(False)
            self.rx = s
            self.last_refresh = self.clock()
            for b in self.beacons.values():
                self._send(b)
        return True

    def disconnect(self):
        """Leave the federation: tombstone every beacon so the PCS clears it [a: Behaviour B2],
        release the port, forget the ownship."""
        if self.rx is None:
            return
        for b in self.beacons.values():
            self._send(b, active=False)
        try:
            self.rx.close()
        except OSError:
            pass
        self.rx = None
        self.ownship = None
        self.last_ownship_t = None
        for b in self.beacons.values():
            b.detected, b.slant_range_m = False, -1.0

    def status(self):
        if self.rx is None:
            return "Disconnected"
        if self.last_ownship_t is not None and self.clock() - self.last_ownship_t < CONNECT_TIMEOUT_S:
            return "Connected"
        return "Waiting for PCS"

    def poll(self):
        """Drain e_ownshipStateIn; periodic refresh. Called by the GUI timer."""
        if self.rx is not None:
            while True:
                try:
                    data = self.rx.recv(65535)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    break
                try:
                    m = msg.decode(data)
                except msg.MessageError:
                    self.rejected += 1
                    continue
                if isinstance(m, msg.OwnshipState):
                    self.on_ownship(m)
        now = self.clock()
        if self.rx is not None and now - self.last_refresh >= msg.BEACON_REFRESH_S:  # [a: Behaviour B2]
            self.last_refresh = now
            for b in self.beacons.values():
                self._send(b)

    # calcBeaconOwnshipRange + calcRadarEquation
    def _compute(self, b: EwBeacon):
        b.max_range_m = (radar.r_max(b.pt_w, b.g_dbi, b.f_hz, b.pmin_dbm, self.rcs_m2)
                         if b.use_radar_eq else b.explicit_range_m)
        if self.ownship is not None:
            o = self.ownship
            b.slant_range_m = radar.slant_range_m(o.lat_deg, o.lon_deg, o.alt_m, b.lat_deg, b.lon_deg, b.alt_m)
            b.detected = b.slant_range_m <= b.max_range_m  # [a: Behaviour B10]
        return b

    def on_ownship(self, o):
        self.ownship = o
        self.last_ownship_t = self.clock()
        for b in self.beacons.values():
            before = (b.detected, round(b.max_range_m, 3))
            self._compute(b)
            if (b.detected, round(b.max_range_m, 3)) != before:
                self._send(b)  # event-driven: only the changed beacon [a: Behaviour B2]

    def _send(self, b: EwBeacon, active=True):
        if self.rx is None:
            return  # not connected: no federation traffic
        m = msg.RadarBeacon(beacon_id=b.beacon_id, classification=b.classification, lat_deg=b.lat_deg,
                            lon_deg=b.lon_deg, alt_m=b.alt_m, max_range_m=b.max_range_m, detected=b.detected,
                            slant_range_m=b.slant_range_m, active=active)
        try:
            self.tx.sendto(msg.encode(m), self.tx_addr)
            self.sent += 1
        except OSError:
            pass

    def reference_position(self):
        if self.ownship is not None:
            return self.ownship.lat_deg, self.ownship.lon_deg, self.ownship.alt_m
        af = SEED[DEFAULT_ICAO]  # [c] no ownship yet: place relative to the default airfield
        return af.lat_deg, af.lon_deg, af.elevation_m

    # manageEwBeacon
    def add_beacon(self, classification, range_m, theta_deg, **params):
        lat0, lon0, alt0 = self.reference_position()
        lat, lon = radar.destination(lat0, lon0, range_m, theta_deg)
        b = EwBeacon(self.next_id, classification, lat, lon, params.pop("alt_m", alt0), **params)
        self.next_id += 1
        self.beacons[b.beacon_id] = self._compute(b)
        self._send(b)
        return b

    def edit_beacon(self, beacon_id, **params):
        b = self.beacons[beacon_id]
        for k, v in params.items():
            setattr(b, k, v)
        self._compute(b)
        self._send(b)
        return b

    def remove_beacon(self, beacon_id):
        b = self.beacons.pop(beacon_id, None)
        if b is not None:
            self._send(b, active=False)  # tombstone [a: Behaviour B2]
        return b

    # controlOwnshipRcs
    def set_rcs(self, rcs_m2):
        self.rcs_m2 = rcs_m2
        for b in self.beacons.values():  # recompute and retransmit every beacon [a: Behaviour B10]
            self._compute(b)
            self._send(b)

    def close(self):
        for s in (self.rx, self.tx):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self.rx = None


# ============================================================================ GUI
def _qt():
    from PyQt5 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


SEM_QSS = """
QWidget { background:#2b2b2b; color:#e0e0e0; font-size:9pt; }
QGroupBox { border:1px solid #555; margin-top:10px; font-weight:bold; }
QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 3px; }
QPushButton { background:#3c3c3c; border:1px solid #666; padding:4px 10px; font-weight:bold; }
QPushButton:hover { background:#4a4a4a; }
QDoubleSpinBox, QSpinBox, QComboBox, QListWidget { background:#1e1e1e; border:1px solid #555; padding:2px; }
"""


def build_sem_window(core: SemCore):
    """updateSEUI: the SEM window. Returns the QMainWindow."""
    QtCore, QtGui, QtWidgets = _qt()

    class SemWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.core = core
            self.setWindowTitle("SEM (Synthetic Environment Manager)")
            self.setStyleSheet(SEM_QSS)
            central = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(central)

            g1 = QtWidgets.QGroupBox("UDP Federation Status")
            h1 = QtWidgets.QHBoxLayout(g1)
            self.btn_connect = QtWidgets.QPushButton("CONNECT TO SIM")
            self.lbl_status = QtWidgets.QLabel("Status: Disconnected")
            h1.addWidget(self.btn_connect)
            h1.addWidget(self.lbl_status, 1)
            v.addWidget(g1)

            g2 = QtWidgets.QGroupBox("Define Emitter Beacon")
            gl = QtWidgets.QGridLayout(g2)

            def dspin(lo, hi, val, dec, suffix, step=1.0):
                s = QtWidgets.QDoubleSpinBox()
                s.setRange(lo, hi); s.setDecimals(dec); s.setValue(val); s.setSuffix(suffix); s.setSingleStep(step)
                return s
            self.cmb_type = QtWidgets.QComboBox()
            self.cmb_type.addItems(["EM_FRIEND", "EM_FOE", "EM_UNKNOWN"])
            self.sp_range = dspin(0, 500000, 5000, 0, " m", 500)
            self.sp_theta = dspin(0, 359, 45, 0, " °", 5)
            self.chk_eq = QtWidgets.QCheckBox("Radar equation")
            self.chk_eq.setChecked(True)
            self.sp_maxr = dspin(100, 1e6, 20000, 0, " m", 1000)
            self.sp_pt = dspin(1, 1e7, radar.DEFAULT_PT_W, 0, " W", 1000)
            self.sp_g = dspin(0, 60, radar.DEFAULT_G_DBI, 1, " dBi", 1)
            self.sp_f = dspin(0.1, 100, radar.DEFAULT_F_HZ / 1e9, 2, " GHz", 0.5)
            self.sp_pmin = dspin(-150, 0, radar.DEFAULT_PMIN_DBM, 1, " dBm", 1)
            self.sp_rcs = dspin(0.001, 1000, core.rcs_m2, 3, " m²", 0.5)
            self.lbl_rmax = QtWidgets.QLabel()
            rows_l = [("Type:", self.cmb_type), ("Initial Range:", self.sp_range), ("Initial Theta:", self.sp_theta),
                      ("", self.chk_eq), ("Max Range:", self.sp_maxr)]
            rows_r = [("Pt (Power):", self.sp_pt), ("G (Gain):", self.sp_g), ("f (Frequency):", self.sp_f),
                      ("Pmin:", self.sp_pmin), ("Ownship RCS (σ):", self.sp_rcs), ("R_max (calc):", self.lbl_rmax)]
            for i, (t, w) in enumerate(rows_l):
                gl.addWidget(QtWidgets.QLabel(t), i, 0); gl.addWidget(w, i, 1)
            for i, (t, w) in enumerate(rows_r):
                gl.addWidget(QtWidgets.QLabel(t), i, 2); gl.addWidget(w, i, 3)
            self.btn_add = QtWidgets.QPushButton("ADD BEACON TO GROUND")
            self.btn_apply = QtWidgets.QPushButton("APPLY TO SELECTED")
            gl.addWidget(self.btn_add, 6, 0, 1, 2)
            gl.addWidget(self.btn_apply, 6, 2, 1, 2)
            v.addWidget(g2)

            g3 = QtWidgets.QGroupBox("Active Emitter Roster (Live Pr Calc)")
            v3 = QtWidgets.QVBoxLayout(g3)
            self.lst = QtWidgets.QListWidget()
            self.lst.setMinimumHeight(90)
            v3.addWidget(self.lst)
            h3 = QtWidgets.QHBoxLayout()
            h3.addWidget(QtWidgets.QLabel("Index:"))
            self.sp_index = QtWidgets.QSpinBox(); self.sp_index.setRange(1, 9999)
            h3.addWidget(self.sp_index, 1)
            self.btn_delete = QtWidgets.QPushButton("DELETE BEACON")
            h3.addWidget(self.btn_delete)
            v3.addLayout(h3)
            v.addWidget(g3, 1)
            self.setCentralWidget(central)

            self.btn_connect.clicked.connect(self.on_connect)
            self.btn_add.clicked.connect(self.on_add)
            self.btn_apply.clicked.connect(self.on_apply)
            self.btn_delete.clicked.connect(self.on_delete)
            self.sp_rcs.valueChanged.connect(lambda val: (core.set_rcs(val), self.refresh()))
            self.lst.currentRowChanged.connect(self.on_select)
            for w in (self.sp_pt, self.sp_g, self.sp_f, self.sp_pmin, self.sp_rcs):
                w.valueChanged.connect(self.update_rmax_label)
            self.chk_eq.toggled.connect(self.sp_maxr.setDisabled)
            self.sp_maxr.setDisabled(True)
            self.update_rmax_label()

            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(50)  # [c] 20 Hz GUI-thread poll of the UDP socket
            self.resize(470, 600)

        def params(self):
            return dict(use_radar_eq=self.chk_eq.isChecked(), explicit_range_m=self.sp_maxr.value(),
                        pt_w=self.sp_pt.value(), g_dbi=self.sp_g.value(), f_hz=self.sp_f.value() * 1e9,
                        pmin_dbm=self.sp_pmin.value())

        def update_rmax_label(self, *_):
            r = radar.r_max(self.sp_pt.value(), self.sp_g.value(), self.sp_f.value() * 1e9, self.sp_pmin.value(), self.sp_rcs.value())
            self.lbl_rmax.setText(f"{r / 1000:.1f} km")

        def on_connect(self):
            """CONNECT TO SIM / DISCONNECT toggle."""
            if core.connected:
                core.disconnect()
            else:
                try:
                    core.connect()
                except OSError as e:
                    self.lbl_status.setText(f"Status: port busy ({e})")
                    self.lbl_status.setStyleSheet("color:#ff5050; font-weight:bold;")
                    return
            self.btn_connect.setText("DISCONNECT" if core.connected else "CONNECT TO SIM")
            self.refresh()
            self.tick()

        def on_add(self):
            cls = self.cmb_type.currentText().replace("EM_", "")
            b = core.add_beacon(cls, self.sp_range.value(), self.sp_theta.value(), **self.params())
            self.refresh()
            self.sp_index.setValue(b.beacon_id)

        def selected_id(self):
            return self.sp_index.value()

        def on_apply(self):
            bid = self.selected_id()
            if bid in core.beacons:
                cls = self.cmb_type.currentText().replace("EM_", "")
                core.edit_beacon(bid, classification=cls, **self.params())
                self.refresh()

        def on_select(self, row):
            ids = sorted(core.beacons)
            if 0 <= row < len(ids):
                b = core.beacons[ids[row]]
                self.sp_index.setValue(b.beacon_id)
                self.cmb_type.setCurrentText("EM_" + b.classification)
                self.chk_eq.setChecked(b.use_radar_eq)
                self.sp_maxr.setValue(b.explicit_range_m)
                self.sp_pt.setValue(b.pt_w); self.sp_g.setValue(b.g_dbi)
                self.sp_f.setValue(b.f_hz / 1e9); self.sp_pmin.setValue(b.pmin_dbm)

        def on_delete(self):
            core.remove_beacon(self.selected_id())
            self.refresh()

        def refresh(self):
            row = self.lst.currentRow()
            self.lst.blockSignals(True)
            self.lst.clear()
            for bid in sorted(core.beacons):
                b = core.beacons[bid]
                sr = f"{b.slant_range_m / 1000:.1f} km" if b.slant_range_m >= 0 else "--"
                self.lst.addItem(f"[{bid}] {b.classification:<7} Rmax {b.max_range_m / 1000:6.1f} km  slant {sr}  "
                                 f"{'DETECTED' if b.detected else 'clear'}")
            if 0 <= row < self.lst.count():
                self.lst.setCurrentRow(row)
            self.lst.blockSignals(False)

        def tick(self):
            core.poll()
            st = core.status()
            color = {"Connected": "#40d040", "Waiting for PCS": "#e0c040"}.get(st, "#ff5050")
            self.lbl_status.setText(f"Status: {st}")
            self.lbl_status.setStyleSheet(f"color:{color}; font-weight:bold;")
            if core.ownship is not None:
                self.refresh()

        def closeEvent(self, ev):
            self.timer.stop()
            core.close()
            super().closeEvent(ev)

    return SemWindow()


class SemApp:
    """Application object: constructs the core and its window. [a: L2SYS-REQ-03.01]"""

    def __init__(self, show=True, autoconnect=False):
        self.core = SemCore()
        if autoconnect:
            try:
                self.core.connect()
            except OSError:
                pass
        self.window = build_sem_window(self.core)
        self.window.btn_connect.setText("DISCONNECT" if self.core.connected else "CONNECT TO SIM")
        if show:
            _fit_to_screen(self.window)
            self.window.show()

    def shutdown(self):
        self.window.timer.stop()
        self.window.close()
        self.core.close()


def _fit_to_screen(w):
    QtCore, QtGui, QtWidgets = _qt()
    scr = QtWidgets.QApplication.primaryScreen()
    if scr is None:
        return
    avail = scr.availableGeometry()
    want = w.size().expandedTo(w.minimumSizeHint())
    w.resize(min(want.width(), avail.width()), min(want.height(), avail.height()))
    w.move(avail.left() + avail.width() - w.width(), avail.top())  # [c] top-right: the PCS occupies the left


def main():
    QtCore, QtGui, QtWidgets = _qt()
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = QtWidgets.QApplication(sys.argv)
    sem = SemApp()
    from flightsim.common import observe
    observe.install(app, lambda: [sem.window], "sem")
    rc = app.exec_()
    sem.core.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
