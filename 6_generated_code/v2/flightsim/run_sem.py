"""SyntheticEnvironmentMgr -- the instructor's station.
[a: fsim_TA_SEM.sysml]

The Qt widget over `flightsim/sem_model.py`, which holds everything computable.
[a: R-7] "Keep the model layer of both free of Qt."

Layout follows the reference screenshot [a: Sec.4.3, authoritative for layout]:

    UDP Federation Status      CONNECT TO SIM | Status: Disconnected
    Define Emitter Beacon      Type / Initial Range / Initial Theta
                               Pt (Power) / G (Gain) / Frequency /
                               Wavelength (derived) / Loss (L) / Pmin
                               ADD BEACON TO GROUND
    Scenario                   Ownship RCS (sigma)
    Active Emitter Roster (Live Pr Calc)
                               Index | DELETE BEACON

Two departures from the screenshot, both reported in docs/MODEL_INTAKE.md F-5
and both required by Appendix A precedence rule 2 (a Decision outranks the
screenshot on **data**; the screenshot outranks a Decision on **layout**):

  * the screenshot's editable `Wavelength` becomes an editable **Frequency**
    with wavelength shown derived -- [a: D12] "Frequency is the editable
    parameter; wavelength is displayed derived";
  * the screenshot's per-beacon `RCS` becomes a **scenario** control --
    [a: D12] "Ownship RCS is a scenario spinbox" and [a: L2SYS-REQ-03.04] "the
    ownship radar cross section as a scenario parameter".

The field order of the screenshot is otherwise preserved.

[a: L2SYS-REQ-03.01] [a: L2SYS-REQ-03.02] [a: L2SYS-REQ-03.03]
[a: L2SYS-REQ-03.04] [a: L2SYS-REQ-03.05]
"""

from __future__ import annotations

import sys

from flightsim.common import ports
from flightsim.common.messages import CLASSIFICATIONS, OwnshipState
from flightsim.sem_model import (
    DEFAULT_FREQ_HZ,
    DEFAULT_GAIN_DBI,
    DEFAULT_LOSS,
    DEFAULT_OWNSHIP_RCS_M2,
    DEFAULT_PMIN_DBM,
    DEFAULT_PT_W,
    Beacon,
    EmitterParameters,
    OwnshipReceiver,
    RefreshTimer,
    Scenario,
    ScenarioPublisher,
    next_beacon_id,
)

#: The dark palette of the reference screenshot. [a: Sec.4.3, layout]
SEM_QSS = """
QWidget { background-color: #1e1e1e; color: #d4d4d4; font-size: 11px; }
QGroupBox { border: 1px solid #3c3c3c; margin-top: 8px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #d4d4d4; }
QPushButton { background-color: #3c3c3c; border: 1px solid #555; padding: 5px; }
QPushButton:hover { background-color: #4a4a4a; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
    background-color: #2d2d2d; border: 1px solid #3c3c3c; padding: 3px;
}
QLabel#statusDisconnected { color: #ef4444; }
QLabel#statusConnected { color: #22c55e; }
"""


def build_sem_window(
    scenario: Scenario | None = None,
    publisher: ScenarioPublisher | None = None,
    receiver_factory=None,
):
    """Construct the SEM window over a scenario. Qt is imported here only."""
    from PyQt5 import QtCore, QtWidgets

    class SemWindow(QtWidgets.QWidget):
        """threadMainGui. [a: fsim_TA_SEM.sysml]"""

        def __init__(self, scenario: Scenario, publisher, receiver_factory):
            super().__init__()
            self.scenario = scenario
            self.publisher = publisher
            self._receiver_factory = receiver_factory
            self.receiver = None
            self.refresh_timer = RefreshTimer()
            self._qtimer = None

            self.setWindowTitle("SEM (Synthetic Environment Manager)")
            self.setStyleSheet(SEM_QSS)
            self._build()
            self._refresh_roster()

        # -- layout ---------------------------------------------------
        def _build(self) -> None:
            outer = QtWidgets.QVBoxLayout(self)

            # ---- UDP Federation Status --------------------------------
            fed = QtWidgets.QGroupBox("UDP Federation Status")
            fed_layout = QtWidgets.QHBoxLayout(fed)
            self.connect_button = QtWidgets.QPushButton("CONNECT TO SIM")
            self.connect_button.setObjectName("connectToSim")
            self.connect_button.clicked.connect(self.connect_to_sim)
            self.status_label = QtWidgets.QLabel("Status: Disconnected")
            self.status_label.setObjectName("statusDisconnected")
            fed_layout.addWidget(self.connect_button)
            fed_layout.addWidget(self.status_label)
            outer.addWidget(fed)

            # ---- Define Emitter Beacon -------------------------------
            box = QtWidgets.QGroupBox("Define Emitter Beacon")
            grid = QtWidgets.QGridLayout(box)

            # [a: L2SYS-REQ-03.02] spawn and classify FRIEND / FOE / UNKNOWN
            self.type_combo = QtWidgets.QComboBox()
            self.type_combo.setObjectName("beaconType")
            for c in CLASSIFICATIONS:
                self.type_combo.addItem(f"EM_{c}", c)
            grid.addWidget(QtWidgets.QLabel("Type:"), 0, 0)
            grid.addWidget(self.type_combo, 0, 1)

            # [a: L2SYS-REQ-03.02] "with defined maximum ranges"
            self.range_spin = self._spin("initialRange", 0.0, 5e6, 5000.0, 0)
            grid.addWidget(QtWidgets.QLabel("Initial Range:"), 1, 0)
            grid.addWidget(self.range_spin, 1, 1)

            self.theta_spin = self._spin("initialTheta", -360.0, 360.0, 45.0, 1)
            grid.addWidget(QtWidgets.QLabel("Initial Theta:"), 2, 0)
            grid.addWidget(self.theta_spin, 2, 1)

            # [a: L2SYS-REQ-03.03] the emitter parameters of the radar equation
            self.pt_spin = self._spin("pt", 0.0, 1e9, DEFAULT_PT_W, 1)
            grid.addWidget(QtWidgets.QLabel("Pt (Power):"), 0, 2)
            grid.addWidget(self.pt_spin, 0, 3)

            self.gain_spin = self._spin("gain", -50.0, 100.0,
                                        DEFAULT_GAIN_DBI, 2)
            grid.addWidget(QtWidgets.QLabel("G (Gain):"), 1, 2)
            grid.addWidget(self.gain_spin, 1, 3)

            # [a: D12] frequency is the EDITABLE parameter (see F-5)
            self.freq_spin = self._spin("frequency", 1e6, 1e12,
                                        DEFAULT_FREQ_HZ, 0)
            grid.addWidget(QtWidgets.QLabel("f (Frequency, Hz):"), 2, 2)
            grid.addWidget(self.freq_spin, 2, 3)

            # [a: D12] wavelength is DISPLAYED DERIVED, never edited
            self.wavelength_label = QtWidgets.QLabel()
            self.wavelength_label.setObjectName("wavelengthDerived")
            grid.addWidget(QtWidgets.QLabel("Wavelength (lambda):"), 3, 2)
            grid.addWidget(self.wavelength_label, 3, 3)
            self.freq_spin.valueChanged.connect(self._update_derived)

            self.loss_spin = self._spin("loss", 0.01, 1e6, DEFAULT_LOSS, 2)
            grid.addWidget(QtWidgets.QLabel("Loss (L):"), 4, 2)
            grid.addWidget(self.loss_spin, 4, 3)

            self.pmin_spin = self._spin("pmin", -200.0, 50.0,
                                        DEFAULT_PMIN_DBM, 1)
            grid.addWidget(QtWidgets.QLabel("Pmin (dBm):"), 5, 2)
            grid.addWidget(self.pmin_spin, 5, 3)

            self.parameterized_check = QtWidgets.QCheckBox(
                "use the radar equation (uncheck to keep the explicit range)"
            )
            self.parameterized_check.setObjectName("useRadarEquation")
            self.parameterized_check.setChecked(True)
            grid.addWidget(self.parameterized_check, 6, 0, 1, 4)

            self.add_button = QtWidgets.QPushButton("ADD BEACON TO GROUND")
            self.add_button.setObjectName("addBeacon")
            self.add_button.clicked.connect(self.add_beacon)
            grid.addWidget(self.add_button, 7, 0, 1, 4)
            outer.addWidget(box)

            # ---- Scenario: ownship RCS [a: L2SYS-REQ-03.04] -----------
            scen = QtWidgets.QGroupBox("Scenario")
            scen_layout = QtWidgets.QHBoxLayout(scen)
            self.rcs_spin = self._spin("ownshipRcs", 0.001, 1e6,
                                       DEFAULT_OWNSHIP_RCS_M2, 3)
            self.rcs_spin.valueChanged.connect(self.on_rcs_changed)
            scen_layout.addWidget(QtWidgets.QLabel("Ownship RCS (sigma, m2):"))
            scen_layout.addWidget(self.rcs_spin)
            outer.addWidget(scen)

            # ---- Active Emitter Roster --------------------------------
            roster_box = QtWidgets.QGroupBox(
                "Active Emitter Roster (Live Pr Calc)"
            )
            roster_layout = QtWidgets.QVBoxLayout(roster_box)
            self.roster = QtWidgets.QListWidget()
            self.roster.setObjectName("activeEmitterRoster")
            roster_layout.addWidget(self.roster)

            row = QtWidgets.QHBoxLayout()
            self.index_spin = QtWidgets.QSpinBox()
            self.index_spin.setObjectName("beaconIndex")
            self.index_spin.setMinimum(1)
            self.index_spin.setMaximum(999)
            self.delete_button = QtWidgets.QPushButton("DELETE BEACON")
            self.delete_button.setObjectName("deleteBeacon")
            self.delete_button.clicked.connect(self.delete_beacon)
            row.addWidget(QtWidgets.QLabel("Index:"))
            row.addWidget(self.index_spin)
            row.addWidget(self.delete_button)
            roster_layout.addLayout(row)
            outer.addWidget(roster_box)

            self._update_derived()

        def _spin(self, name, lo, hi, value, decimals):
            from PyQt5 import QtWidgets as W

            s = W.QDoubleSpinBox()
            s.setObjectName(name)
            s.setDecimals(decimals)
            s.setRange(lo, hi)
            s.setValue(value)
            return s

        # -- updateSEUI ------------------------------------------------
        def _update_derived(self) -> None:
            """[a: D12] wavelength displayed derived from frequency."""
            from flightsim.sem_model import wavelength_m

            lam = wavelength_m(self.freq_spin.value())
            self.wavelength_label.setText(f"{lam:.6f} m")

        def _refresh_roster(self) -> None:
            """[a: D2] "Every display of the beacon roster reflects both its
            insertions and its removals.\""""
            self.roster.clear()
            rcs = self.scenario.ownship_rcs_m2
            for i, b in enumerate(self.scenario.beacons, start=1):
                msg = self.scenario.to_message(b)
                self.roster.addItem(
                    f"{i}. {b.beacon_id}  {b.classification}  "
                    f"Rmax={msg.max_range_m / 1000.0:.2f} km  "
                    f"slant={msg.slant_range_m / 1000.0:.2f} km  "
                    f"{'DETECTED' if msg.detected else '---'}"
                )
            self.index_spin.setMaximum(max(1, len(self.scenario.beacons)))
            _ = rcs

        # -- connectToSim ----------------------------------------------
        def connect_to_sim(self) -> None:
            """[a: fsim_TA_SEM.sysml] connectToSim."""
            if self.receiver is not None:
                return
            factory = self._receiver_factory or (
                lambda cb: OwnshipReceiver(cb, port=ports.ORCH_TO_SEM)
            )
            self.receiver = factory(self.on_ownship_state)
            start = getattr(self.receiver, "start", None)
            if start:
                start()
            self.status_label.setText("Status: Connected")
            self.status_label.setObjectName("statusConnected")
            self.status_label.setStyleSheet("color: #22c55e;")
            self._start_refresh()

        def _start_refresh(self) -> None:
            """[a: D2] "Background refresh every 2.0 s.\""""
            from PyQt5 import QtCore as C

            self._qtimer = C.QTimer(self)
            self._qtimer.setInterval(int(self.refresh_timer.period_s * 1000))
            self._qtimer.timeout.connect(self.on_refresh)
            self._qtimer.start()

        def on_refresh(self) -> None:
            self.scenario.refresh_all()
            self._refresh_roster()

        def on_ownship_state(self, state: OwnshipState) -> None:
            """[a: L2SYS-REQ-03.05] "against the received ownship state\""""
            self.scenario.update_ownship(state)
            self._refresh_roster()

        # -- manageEwBeacon --------------------------------------------
        def add_beacon(self) -> Beacon:
            """[a: L2SYS-REQ-03.02] spawn and classify."""
            classification = self.type_combo.currentData()
            lat, lon = self._placement()
            emitter = None
            if self.parameterized_check.isChecked():
                emitter = EmitterParameters(
                    pt_w=self.pt_spin.value(),
                    gain_dbi=self.gain_spin.value(),
                    frequency_hz=self.freq_spin.value(),
                    pmin_dbm=self.pmin_spin.value(),
                    loss=self.loss_spin.value(),
                )
            beacon = Beacon(
                beacon_id=next_beacon_id(
                    b.beacon_id for b in self.scenario.beacons
                ),
                classification=classification,
                lat=lat, lon=lon, alt=0.0,
                explicit_range_m=self.range_spin.value(),
                emitter=emitter,
            )
            self.scenario.add(beacon)
            self._refresh_roster()
            return beacon

        def _placement(self) -> tuple[float, float]:
            """Place the beacon at Initial Range on Initial Theta from the
            ownship, which is what those two screenshot fields mean. [b:
            Sec.4.3]"""
            import math

            from flightsim.sem_model import EARTH_RADIUS_M

            own = self.scenario.ownship
            lat0 = own.lat_deg if own else 45.4451
            lon0 = own.lon_deg if own else 9.2767
            rng = self.range_spin.value()
            theta = math.radians(self.theta_spin.value())
            dnorth = rng * math.cos(theta)
            deast = rng * math.sin(theta)
            dlat = math.degrees(dnorth / EARTH_RADIUS_M)
            dlon = math.degrees(
                deast / (EARTH_RADIUS_M * math.cos(math.radians(lat0)))
            )
            return lat0 + dlat, lon0 + dlon

        def delete_beacon(self) -> None:
            """[a: D2] deletion publishes a tombstone."""
            beacons = self.scenario.beacons
            idx = self.index_spin.value() - 1
            if not (0 <= idx < len(beacons)):
                return
            self.scenario.remove(beacons[idx].beacon_id)
            self._refresh_roster()

        # -- controlOwnshipRcs -----------------------------------------
        def on_rcs_changed(self, value: float) -> None:
            """[a: L2SYS-REQ-03.04] [a: D12] "a change of RCS recomputes and
            retransmits every beacon.\""""
            if value <= 0.0:
                return
            self.scenario.set_ownship_rcs(value)
            self._refresh_roster()

        def closeEvent(self, event):          # noqa: N802 - Qt naming
            if self._qtimer is not None:
                self._qtimer.stop()
            if self.receiver is not None:
                stop = getattr(self.receiver, "stop", None)
                if stop:
                    stop()
            super().closeEvent(event)

    scenario = scenario if scenario is not None else Scenario(
        publish=publisher
    )
    return SemWindow(scenario, publisher, receiver_factory)


def main(argv: list[str] | None = None) -> int:
    from PyQt5 import QtWidgets

    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    publisher = ScenarioPublisher()
    scenario = Scenario(publish=publisher)
    window = build_sem_window(scenario=scenario, publisher=publisher)
    window.resize(520, 780)
    window.show()
    try:
        return app.exec_()
    finally:
        publisher.close()


if __name__ == "__main__":
    raise SystemExit(main())
