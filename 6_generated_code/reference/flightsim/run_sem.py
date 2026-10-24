"""
flightsim.run_sem
=================
SyntheticEnvironmentMgr: radar/EW simulation with instructor GUI.

TRACEABILITY
------------
[a] Implements part def SyntheticEnvironmentMgr (fsim_TA_SEM.sysml)
    :> SyntheticEnvironment (fsim_LA.sysml). TA doc names this file:
    "run_sem.py". Runtime: Python 3.14 main venv [a: TA attribute].
    Single GUI thread per TA part threadMainGui.
[a] Ports (fsim_TA_SEM.sysml, port erratum fixed 2026-07-11):
      e_ownshipStateIn     UDP rx bind 5007  <- orchestrator
      e_radarDetectionsOut UDP tx to 6007    -> orchestrator
[a] Requirements: ScenarioManagementReq / req_scenarioManagement ("allow
    an instructor to inject and remove radar beacons dynamically via a
    GUI"); L2SYS-REQ-03.02 (FRIEND/FOE/UNKNOWN classification, defined
    maximum range); L2SYS-REQ-03.05 (detection status via the MONOSTATIC
    RADAR EQUATION, rev. 2026-07-11); L2SYS-REQ-03.03 (per-beacon emitter
    parameters Pt/G/f/Pmin adjustable in the GUI); L2SYS-REQ-03.04
    (ownship RCS as an instructor-adjustable scenario parameter).
    Use case UC_BeaconInjection (actor: instructor).
[a] Beacon wire protocol per common/messages.py (customer decisions
    2026-07-11): event-driven single-beacon datagrams, tombstone on
    delete, background refresh every BEACON_REFRESH_PERIOD_S.

TA ACTION -> CODE MAPPING [b]
-----------------------------
  threadMainGui.connectToSim          -> SemWindow._poll_ownship (QTimer,
                                         non-blocking UDP drain)
  threadMainGui.manageEwScenario
    .manageEwBeacon                   -> add/edit/remove beacon UI handlers
    .controlOwnshipRcs                -> RCS spinbox [a: 03.3]
  threadMainGui.calcBeaconOwnshipRange-> slant_range_m()
  threadMainGui.calcRadarEquation     -> radar_max_range_m() [a: 02.2];
                                         detection = slant <= R_max
                                         (equivalent to Pr >= Pmin)
  threadMainGui.updateSEUI            -> SemWindow._refresh_table (QTimer)

TRANSMIT TRIGGERS [a: protocol] -> [b: realized as]
---------------------------------------------------
  1. beacon created / edited          -> immediate tx (event)
  2. beacon deleted                   -> immediate tombstone tx
  3. detection status flip            -> immediate tx (event)
  4. every BEACON_REFRESH_PERIOD_S    -> re-tx all live beacons (repair)

DEVELOPER CHOICES [c] (flagged inline)
--------------------------------------
- Slant range: WGS84 haversine ground distance + altitude delta,
  root-sum-square. Adequate at radar ranges << Earth radius; no
  atmospheric/horizon modelling (out of scope for the requirement).
- Default beacon spawn: 20 km ahead of the last known ownship position
  (or Milano area if none), max range 25 km -- editable in the dialog.
- Radar equation form [b]: monostatic, R_max = ((Pt G^2 lambda^2 sigma)
  / ((4 pi)^3 Pmin))^(1/4); detection criterion slant <= R_max is
  mathematically equivalent to received power >= Pmin. No losses /
  noise-figure / atmospheric terms (not in the requirements; add L as a
  divisor later if wanted). max_range_m in the wire message is now the
  COMPUTED effective range -- the PCS threat rings resize with emitter
  power and RCS with zero PCS-side changes.
- Emitter parameters live SEM-side only (self.emitters); the wire
  interface (RadarBeacon) is unchanged by design.
- Default emitter [c]: Pt=100 kW, G=35 dBi, f=10 GHz, Pmin=-90 dBm;
  with default RCS 5 m^2 -> R_max ~= 39 km.
"""

from __future__ import annotations

import math
import socket
import sys
import time

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel, QDialog,
    QFormLayout, QComboBox, QDoubleSpinBox, QLineEdit, QDialogButtonBox,
    QStatusBar)

from flightsim.common import ports
from flightsim.common.messages import (
    OwnshipState, RadarBeacon, BeaconClass, MessageError,
    BEACON_REFRESH_PERIOD_S)

EARTH_R_M = 6371000.0
C_LIGHT = 299792458.0


def radar_max_range_m(pt_w: float, gain_dbi: float, freq_hz: float,
                      pmin_dbm: float, rcs_m2: float) -> float:
    """calcRadarEquation [a: L2SYS-REQ-03.05]: monostatic radar equation,
    solved for maximum detection range.
        R_max = ( Pt * G^2 * lambda^2 * sigma / ((4*pi)^3 * Pmin) )^(1/4)
    Units in: W, dBi, Hz, dBm, m^2. Out: meters."""
    g = 10.0 ** (gain_dbi / 10.0)
    pmin_w = 10.0 ** (pmin_dbm / 10.0) / 1000.0
    lam = C_LIGHT / freq_hz
    r4 = (pt_w * g * g * lam * lam * rcs_m2) / ((4 * math.pi) ** 3 * pmin_w)
    return r4 ** 0.25


from dataclasses import dataclass, field as _field


@dataclass
class EmitterParams:
    """Per-beacon radar-equation parameters [a: L2SYS-REQ-03.03].
    SEM-internal only; not part of the wire interface."""
    pt_w: float = 100e3        # transmit power [c default]
    gain_dbi: float = 35.0     # antenna gain
    freq_ghz: float = 10.0     # frequency
    pmin_dbm: float = -90.0    # receiver sensitivity


def slant_range_m(lat1, lon1, alt1, lat2, lon2, alt2) -> float:
    """calcBeaconOwnshipRange [a]: haversine ground distance + altitude
    delta, RSS. [c] flat-slant approximation, fine for radar ranges."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    ground = 2 * EARTH_R_M * math.asin(math.sqrt(a))
    return math.hypot(ground, alt2 - alt1)


class BeaconDialog(QDialog):
    """manageEwBeacon [a]: beacon creation/edit dialog with per-beacon
    radar-equation emitter parameters [a: L2SYS-REQ-03.03] and a live
    computed R_max readout."""

    def __init__(self, parent, default_id: str, near: OwnshipState | None,
                 rcs_m2: float, existing: RadarBeacon | None = None,
                 existing_em: EmitterParams | None = None):
        super().__init__(parent)
        self._rcs = rcs_m2
        edit = existing is not None
        self.setWindowTitle("Edit Beacon" if edit else "Inject Beacon")
        form = QFormLayout(self)
        self.ed_id = QLineEdit(existing.beacon_id if edit else default_id)
        self.ed_id.setReadOnly(edit)      # id is the table key [b]
        self.cb_class = QComboBox()
        self.cb_class.addItems([c.value for c in BeaconClass])
        if edit:
            self.cb_class.setCurrentText(existing.classification)
        # [c] spawn default: ~20 km north of ownship, or Milano area
        lat0 = existing.lat_deg if edit else (
            near.lat_deg + 0.18 if near else 45.60)
        lon0 = existing.lon_deg if edit else (
            near.lon_deg if near else 9.28)
        alt0 = existing.alt_m if edit else 200.0
        em = existing_em or EmitterParams()
        self.sp_lat = QDoubleSpinBox(); self.sp_lat.setRange(-90, 90)
        self.sp_lat.setDecimals(5); self.sp_lat.setValue(lat0)
        self.sp_lon = QDoubleSpinBox(); self.sp_lon.setRange(-180, 180)
        self.sp_lon.setDecimals(5); self.sp_lon.setValue(lon0)
        self.sp_alt = QDoubleSpinBox(); self.sp_alt.setRange(0, 10000)
        self.sp_alt.setValue(alt0)
        # Emitter parameters [a: L2SYS-REQ-03.03]
        self.sp_pt = QDoubleSpinBox(); self.sp_pt.setRange(0.001, 100000)
        self.sp_pt.setDecimals(3); self.sp_pt.setValue(em.pt_w / 1000.0)
        self.sp_pt.setSuffix(" kW")
        self.sp_gain = QDoubleSpinBox(); self.sp_gain.setRange(0, 60)
        self.sp_gain.setValue(em.gain_dbi); self.sp_gain.setSuffix(" dBi")
        self.sp_freq = QDoubleSpinBox(); self.sp_freq.setRange(0.03, 100)
        self.sp_freq.setDecimals(3); self.sp_freq.setValue(em.freq_ghz)
        self.sp_freq.setSuffix(" GHz")
        self.sp_pmin = QDoubleSpinBox(); self.sp_pmin.setRange(-160, 0)
        self.sp_pmin.setValue(em.pmin_dbm); self.sp_pmin.setSuffix(" dBm")
        self.lbl_rmax = QLabel("")
        form.addRow("Beacon ID:", self.ed_id)
        form.addRow("Classification:", self.cb_class)
        form.addRow("Latitude [deg]:", self.sp_lat)
        form.addRow("Longitude [deg]:", self.sp_lon)
        form.addRow("Altitude MSL [m]:", self.sp_alt)
        form.addRow("Tx power Pt:", self.sp_pt)
        form.addRow("Antenna gain G:", self.sp_gain)
        form.addRow("Frequency f:", self.sp_freq)
        form.addRow("Rx sensitivity Pmin:", self.sp_pmin)
        form.addRow("Computed R_max:", self.lbl_rmax)
        for w in (self.sp_pt, self.sp_gain, self.sp_freq, self.sp_pmin):
            w.valueChanged.connect(self._update_rmax)
        self._update_rmax()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def emitter(self) -> EmitterParams:
        return EmitterParams(pt_w=self.sp_pt.value() * 1000.0,
                             gain_dbi=self.sp_gain.value(),
                             freq_ghz=self.sp_freq.value(),
                             pmin_dbm=self.sp_pmin.value())

    def _update_rmax(self) -> None:
        em = self.emitter()
        r = radar_max_range_m(em.pt_w, em.gain_dbi, em.freq_ghz * 1e9,
                              em.pmin_dbm, self._rcs)
        self.lbl_rmax.setText(f"{r/1000:.1f} km  (RCS {self._rcs:g} m2)")

    def beacon(self) -> RadarBeacon:
        return RadarBeacon(
            beacon_id=self.ed_id.text().strip() or "BCN",
            classification=self.cb_class.currentText(),
            lat_deg=self.sp_lat.value(), lon_deg=self.sp_lon.value(),
            alt_m=self.sp_alt.value())


class SemWindow(QMainWindow):
    """threadMainGui [a]: EW scenario state, detection math, and UI."""

    COLS = ["ID", "Class", "Lat", "Lon", "Alt m",
            "Max rng m", "Slant rng m", "Detected"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SEM - Synthetic Environment Manager (Instructor)")

        self.beacons: dict[str, RadarBeacon] = {}      # EW scenario state
        self.emitters: dict[str, EmitterParams] = {}   # [a: 03.2] SEM-side
        self.rcs_m2 = 5.0                              # [a: 03.3] [c default]
        self.ownship: OwnshipState | None = None
        self._seq = 0
        self._next_id = 1

        # e_ownshipStateIn : bind 5007 [a]
        self._rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx.bind((ports.LOCALHOST, ports.UDP_SEM_OWNSHIP_STATE))
        self._rx.setblocking(False)
        # e_radarDetectionsOut : tx -> 6007 [a]
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self._build_ui()

        # connectToSim + calcBeaconOwnshipRange [a] -- 20 Hz [c]
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_ownship)
        self._poll_timer.start(50)
        # updateSEUI [a] -- 5 Hz [c]
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._refresh_table)
        self._ui_timer.start(200)
        # background refresh (loss repair) [a: protocol trigger 4]
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_all_beacons)
        self._refresh_timer.start(int(BEACON_REFRESH_PERIOD_S * 1000))

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        self.lbl_own = QLabel("Ownship: (no data)  --  link down?")
        root.addWidget(self.lbl_own)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.table)
        bar = QHBoxLayout()
        btn_add = QPushButton("Inject Beacon...")
        btn_edit = QPushButton("Edit Selected...")
        btn_del = QPushButton("Remove Selected")
        btn_add.clicked.connect(self._add_beacon)      # manageEwBeacon [a]
        btn_edit.clicked.connect(self._edit_selected)  # "adjust" [a: 03.2]
        btn_del.clicked.connect(self._remove_selected)
        bar.addWidget(btn_add); bar.addWidget(btn_edit); bar.addWidget(btn_del)
        # controlOwnshipRcs [a: L2SYS-REQ-03.04]
        bar.addSpacing(24)
        bar.addWidget(QLabel("Ownship RCS:"))
        self.sp_rcs = QDoubleSpinBox()
        self.sp_rcs.setRange(0.01, 1000.0); self.sp_rcs.setDecimals(2)
        self.sp_rcs.setValue(self.rcs_m2); self.sp_rcs.setSuffix(" m2")
        self.sp_rcs.valueChanged.connect(self._on_rcs_changed)
        bar.addWidget(self.sp_rcs)
        bar.addStretch()
        root.addLayout(bar)
        self.setStatusBar(QStatusBar())
        self.setCentralWidget(central)
        self.resize(720, 420)

    # ------------------------------------------------------------------
    # calcRadarEquation [a: L2SYS-REQ-03.05]
    # ------------------------------------------------------------------
    def _recompute_beacon(self, b: RadarBeacon) -> None:
        """Effective R_max from emitter params + scenario RCS; published
        in the existing max_range_m field (wire interface unchanged)."""
        em = self.emitters.get(b.beacon_id)
        if em is None:
            return          # beacon without emitter params keeps its range
        b.max_range_m = radar_max_range_m(
            em.pt_w, em.gain_dbi, em.freq_ghz * 1e9, em.pmin_dbm,
            self.rcs_m2)
        if b.slant_range_m >= 0:
            b.detected = b.slant_range_m <= b.max_range_m

    def _on_rcs_changed(self, v: float) -> None:       # [a: 03.3]
        self.rcs_m2 = v
        for b in self.beacons.values():                # RCS affects all
            self._recompute_beacon(b)
            self._tx_beacon(b)                         # edit event [a]

    # ------------------------------------------------------------------
    # e_radarDetectionsOut transmission
    # ------------------------------------------------------------------
    def _tx_beacon(self, b: RadarBeacon) -> None:
        self._seq += 1
        b.seq = self._seq
        b.sim_time_s = self.ownship.sim_time_s if self.ownship else 0.0
        self._tx.sendto(b.to_json(),
                        (ports.LOCALHOST, ports.UDP_SEM_RADAR_DETECTIONS))

    def _refresh_all_beacons(self) -> None:            # trigger 4 [a]
        for b in self.beacons.values():
            self._tx_beacon(b)

    # ------------------------------------------------------------------
    # connectToSim + calcBeaconOwnshipRange [a]
    # ------------------------------------------------------------------
    def _poll_ownship(self) -> None:
        got = False
        try:
            while True:
                raw, _ = self._rx.recvfrom(65535)
                try:
                    self.ownship = OwnshipState.from_json(raw)
                    got = True
                except MessageError:
                    continue
        except BlockingIOError:
            pass
        if got and self.ownship is not None:
            o = self.ownship
            for b in self.beacons.values():
                b.slant_range_m = slant_range_m(
                    b.lat_deg, b.lon_deg, b.alt_m,
                    o.lat_deg, o.lon_deg, o.alt_m)
                was = b.detected
                b.detected = b.slant_range_m <= b.max_range_m
                if b.detected != was:
                    self._tx_beacon(b)                 # trigger 3 [a]

    # ------------------------------------------------------------------
    # manageEwScenario [a]: instructor inject / remove
    # ------------------------------------------------------------------
    def _add_beacon(self) -> None:
        dlg = BeaconDialog(self, f"BCN-{self._next_id:02d}", self.ownship,
                           self.rcs_m2)
        if dlg.exec_() != QDialog.Accepted:
            return
        b = dlg.beacon()
        if b.beacon_id in self.beacons:
            self.statusBar().showMessage(
                f"Beacon id {b.beacon_id!r} already exists.", 4000)
            return
        self._next_id += 1
        self.emitters[b.beacon_id] = dlg.emitter()     # [a: 03.2]
        # initial slant + radar-equation state before first tx [b]
        if self.ownship is not None:
            b.slant_range_m = slant_range_m(
                b.lat_deg, b.lon_deg, b.alt_m,
                self.ownship.lat_deg, self.ownship.lon_deg,
                self.ownship.alt_m)
        self._recompute_beacon(b)                      # [a: 02.2]
        self.beacons[b.beacon_id] = b
        self._tx_beacon(b)                             # trigger 1 [a]
        self.statusBar().showMessage(f"Injected {b.beacon_id}", 3000)

    def _edit_selected(self) -> None:                  # "adjust" [a: 03.2]
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1:
            self.statusBar().showMessage("Select exactly one beacon.", 3000)
            return
        bid = self.table.item(next(iter(rows)), 0).text()
        b = self.beacons.get(bid)
        if b is None:
            return
        dlg = BeaconDialog(self, bid, self.ownship, self.rcs_m2,
                           existing=b, existing_em=self.emitters.get(bid))
        if dlg.exec_() != QDialog.Accepted:
            return
        nb = dlg.beacon()
        b.classification = nb.classification
        b.lat_deg, b.lon_deg, b.alt_m = nb.lat_deg, nb.lon_deg, nb.alt_m
        self.emitters[bid] = dlg.emitter()
        if self.ownship is not None:
            b.slant_range_m = slant_range_m(
                b.lat_deg, b.lon_deg, b.alt_m,
                self.ownship.lat_deg, self.ownship.lon_deg,
                self.ownship.alt_m)
        self._recompute_beacon(b)
        self._tx_beacon(b)                             # edit event [a]
        self.statusBar().showMessage(f"Updated {bid}", 3000)

    def _remove_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            bid = self.table.item(row, 0).text()
            b = self.beacons.pop(bid, None)
            self.emitters.pop(bid, None)
            if b is not None:
                b.active = False
                self._tx_beacon(b)                     # trigger 2 [a]
                self.statusBar().showMessage(f"Removed {bid}", 3000)

    # ------------------------------------------------------------------
    # updateSEUI [a]
    # ------------------------------------------------------------------
    def _refresh_table(self) -> None:
        if self.ownship is not None:
            o = self.ownship
            self.lbl_own.setText(
                f"Ownship: {o.lat_deg:.4f}, {o.lon_deg:.4f}  "
                f"alt {o.alt_m:.0f} m  hdg {o.heading_deg:.0f}  "
                f"(sim t={o.sim_time_s:.0f}s)")
        self.table.setRowCount(len(self.beacons))
        for row, b in enumerate(self.beacons.values()):
            vals = [b.beacon_id, b.classification,
                    f"{b.lat_deg:.4f}", f"{b.lon_deg:.4f}",
                    f"{b.alt_m:.0f}", f"{b.max_range_m:.0f}",
                    f"{b.slant_range_m:.0f}" if b.slant_range_m >= 0 else "-",
                    "YES" if b.detected else "no"]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if col == 7 and b.detected:
                    item.setBackground(Qt.red)
                self.table.setItem(row, col, item)

    def closeEvent(self, event) -> None:
        # [c] tombstone everything on exit so the PCS map clears promptly
        for b in list(self.beacons.values()):
            b.active = False
            self._tx_beacon(b)
        self._rx.close()
        self._tx.close()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    win = SemWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()