"""UserInterfaceManager — the PCS window. [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#UserInterfaceManager]

controlUserInputs (controlManualFlight, controlFlightSystems, controlMissionSettings,
controlNavigationAP/AH/AT), updateCockpitDisplays, updateAttentionGetters, manageSSF.
Arrangement per Desired_UIUX_capture.JPG (F-16 PCS window). The instrument column is laid
out in two columns so the window fits 1280×650 logical (discrepancy listed in
docs/OBSERVATION.md).
[a: L2SYS-REQ-01.06] [a: L2SYS-REQ-01.08] [a: L2SYS-REQ-01.08c] [a: L2SYS-REQ-01.10] [a: L2SYS-REQ-01.11]
"""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

from flightsim.pcs.instruments import (AttitudeIndicator, HeadingIndicator, RoundGauge, RpmBar,
                                       HudView, AttentionStrip)
from flightsim.pcs.virtual_hotas import VirtualHotas

PCS_QSS = """
QMainWindow, QWidget { background:#000; color:#00e03c; font-family:'Consolas','Courier New'; font-size:8pt; }
QGroupBox { border:1px solid #555; margin-top:9px; font-weight:bold; color:#00e03c; }
QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 3px; }
QPushButton { background:#000; color:#00e03c; border:1px solid #666; padding:3px 6px; }
QPushButton:checked { background:#0a3; color:#000; }
QPushButton:disabled { color:#444; border-color:#333; }
QLineEdit, QComboBox, QDoubleSpinBox, QPlainTextEdit { background:#000; color:#00e03c; border:1px solid #666; padding:1px; }
QMenuBar { background:#111; color:#ddd; } QMenuBar::item:selected { background:#333; }
QMenu { background:#111; color:#ddd; } QMenu::item:selected { background:#333; }
"""

SSF_BUTTONS = [  # [a: L2SYS-REQ-01.10b] labels as the screenshot
    ("run", "RUN SIM"), ("pause", "PAUSE SIM"), ("reset", "RESET SIM"), ("launch", "LAUNCH 3D OTW"),
    ("set_position", "SET POSITION"), ("save", "Save Snapshot"), ("load", "Load Snapshot"),
    ("fuel_freeze", "FUEL FREEZE"), ("capture", "Telemetry Capture")]
FREEZE_GATED = {"reset", "set_position", "save", "load"}  # [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#manageSSF caveats]
CHECKABLE = {"fuel_freeze", "capture"}


def _style(btn, bg, fg="#000", border=None):
    btn.setStyleSheet(f"QPushButton{{background:{bg};color:{fg};border:1px solid {border or bg};font-weight:bold;padding:6px 2px;}}")


class UserInterfaceManager(QtWidgets.QMainWindow):
    ssf_requested = QtCore.pyqtSignal(str)
    closed = QtCore.pyqtSignal()

    def sizeHint(self):
        return QtCore.QSize(1000, 620)  # preferred; clamped to the screen by the application

    def closeEvent(self, ev):
        self.closed.emit()
        super().closeEvent(ev)

    def __init__(self, airfields, default_icao):
        super().__init__()
        self.setWindowTitle("F-16 PCS (Pilot Control Station)")
        self.setStyleSheet(PCS_QSS)
        self.airfields = airfields
        central = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        left = QtWidgets.QVBoxLayout()
        root.addLayout(left, 1)

        # attention getter strip [a: L2SYS-REQ-02.04]
        self.attention = AttentionStrip()
        left.addWidget(self.attention)

        row = QtWidgets.QHBoxLayout()
        left.addLayout(row, 1)
        # virtual HOTAS [a: L2SYS-REQ-01.08b]
        self.vhotas = VirtualHotas()
        self.vhotas.throttle_box.setFixedWidth(72)
        row.addWidget(self.vhotas.throttle_box)
        self.vhotas.stick_box.setMinimumWidth(165)
        row.addWidget(self.vhotas.stick_box, 1)

        mid = QtWidgets.QVBoxLayout()
        row.addLayout(mid)
        # System Controls [a: L2SYS-REQ-01.06]
        self.sys_box = QtWidgets.QGroupBox("System Controls")
        self.sys_box.setObjectName("system_controls_panel")
        sg = QtWidgets.QGridLayout(self.sys_box)
        self.btn_prkbrk = QtWidgets.QPushButton(); self.btn_prkbrk.setObjectName("btn_park_brake")
        self.btn_engine = QtWidgets.QPushButton(); self.btn_engine.setObjectName("btn_engine")
        self.btn_airbrake = QtWidgets.QPushButton(); self.btn_airbrake.setObjectName("btn_air_brake")
        self.btn_gear = QtWidgets.QPushButton(); self.btn_gear.setObjectName("btn_ldg_gear")
        for b in (self.btn_prkbrk, self.btn_engine, self.btn_airbrake, self.btn_gear):
            b.setMinimumWidth(112)
        sg.addWidget(self.btn_prkbrk, 0, 0); sg.addWidget(self.btn_engine, 0, 1)
        sg.addWidget(self.btn_airbrake, 1, 0); sg.addWidget(self.btn_gear, 1, 1)
        mid.addWidget(self.sys_box)

        # Navigation Controls [a: L2SYS-REQ-01.11]
        self.nav_box = QtWidgets.QGroupBox("Navigation Controls")
        self.nav_box.setObjectName("navigation_controls_panel")
        ng = QtWidgets.QGridLayout(self.nav_box)
        ng.setVerticalSpacing(3)
        self.fields = {}
        for r, (key, lab, lo, hi, dec, step) in enumerate((
                ("alt", "Alt", -500, 20000, 0, 100), ("spd", "Spd", 0, 700, 0, 10), ("hdg", "Hdg", 0, 359, 0, 5))):
            ng.addWidget(QtWidgets.QLabel(lab), r, 0)
            minus = QtWidgets.QPushButton("-"); plus = QtWidgets.QPushButton("+")
            for b in (minus, plus):
                b.setFixedWidth(22)
            sp = QtWidgets.QDoubleSpinBox(); sp.setRange(lo, hi); sp.setDecimals(dec); sp.setSingleStep(step)
            sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons); sp.setObjectName(f"field_{key}")
            if key == "hdg":
                sp.setWrapping(True)
            minus.clicked.connect(lambda _, s=sp: s.stepDown())
            plus.clicked.connect(lambda _, s=sp: s.stepUp())
            ng.addWidget(minus, r, 1); ng.addWidget(sp, r, 2); ng.addWidget(plus, r, 3)
            self.fields[key] = sp
        for r, (key, lab) in enumerate((("lat", "Lat"), ("lon", "Lon")), start=3):
            ng.addWidget(QtWidgets.QLabel(lab), r, 0)
            sp = QtWidgets.QDoubleSpinBox(); sp.setDecimals(4)
            sp.setRange(-90, 90) if key == "lat" else sp.setRange(-180, 180)
            sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons); sp.setObjectName(f"field_{key}")
            ng.addWidget(sp, r, 1, 1, 3)
            self.fields[key] = sp
        apl = QtWidgets.QHBoxLayout()
        self.btn_ap = QtWidgets.QPushButton("AP"); self.btn_ah = QtWidgets.QPushButton("AH"); self.btn_at = QtWidgets.QPushButton("AT")
        for n, b in (("ap", self.btn_ap), ("ah", self.btn_ah), ("at", self.btn_at)):
            b.setCheckable(True); b.setObjectName(f"btn_{n}"); apl.addWidget(b)
        ng.addLayout(apl, 5, 0, 1, 4)
        mid.addWidget(self.nav_box, 1)

        # Mission Control [a: L2SYS-REQ-01.04] [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#ssfSelectAirport]
        self.mission_box = QtWidgets.QGroupBox("Mission Control")
        ml = QtWidgets.QVBoxLayout(self.mission_box)
        ml.addWidget(QtWidgets.QLabel("Location:"))
        self.cmb_airport = QtWidgets.QComboBox(); self.cmb_airport.setObjectName("cmb_airport")
        for icao, af in airfields.items():
            self.cmb_airport.addItem(f"{af.name} ({icao})", icao)
        self.cmb_airport.setCurrentIndex(max(0, self.cmb_airport.findData(default_icao)))
        ml.addWidget(self.cmb_airport)
        self.lbl_state = QtWidgets.QLabel("SIM: FROZEN")
        ml.addWidget(self.lbl_state)
        self.lbl_hotas = QtWidgets.QLabel("HOTAS: VIRTUAL")
        ml.addWidget(self.lbl_hotas)
        self.lbl_threats = QtWidgets.QLabel("THREATS: 0")
        ml.addWidget(self.lbl_threats)
        ml.addStretch(1)
        self.cmb_airport.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.mission_box.setFixedWidth(170)
        row.addWidget(self.mission_box)

        # Sim Control [a: L2SYS-REQ-01.10]
        self.sim_box = QtWidgets.QGroupBox("Sim Control")
        self.sim_box.setObjectName("sim_control_panel")
        gg = QtWidgets.QGridLayout(self.sim_box)
        gg.setSpacing(4)
        self.ssf_buttons = {}
        for i, (key, text) in enumerate(SSF_BUTTONS):
            b = QtWidgets.QPushButton(text); b.setObjectName(f"ssf_{key}")
            b.setCheckable(key in CHECKABLE)
            b.clicked.connect(lambda _, k=key: self.ssf_requested.emit(k))
            gg.addWidget(b, i // 5, i % 5)
            self.ssf_buttons[key] = b
        left.addWidget(self.sim_box)

        # Message Window [a: L2SYS-REQ-01.08c]
        self.messages = QtWidgets.QPlainTextEdit(); self.messages.setReadOnly(True)
        self.messages.setObjectName("message_window")
        self.messages.setFixedHeight(58)
        self.messages.setMaximumBlockCount(500)
        left.addWidget(self.messages)

        # Instruments [a: L2SYS-REQ-01.08c]
        inst = QtWidgets.QGridLayout()
        inst.setSpacing(4)
        self.adi = AttitudeIndicator()
        self.hdg = HeadingIndicator()
        self.asi = RoundGauge("Airspeed", "m/s", 400.0, secondary=("kts", 1.943844))
        self.alt = RoundGauge("Altitude", "m", 15000.0, secondary=("ft", 3.280840))
        self.fuel = RoundGauge("Fuel", "lbs", 7000.0)
        self.rpm = RpmBar()
        self.hud = HudView()
        # HUD on top of the rightmost column, flight instruments below it (practitioner request)
        inst.addWidget(self.hud, 0, 0, 1, 2)
        inst.addWidget(self.adi, 1, 0); inst.addWidget(self.hdg, 1, 1)
        inst.addWidget(self.asi, 2, 0); inst.addWidget(self.alt, 2, 1)
        inst.addWidget(self.fuel, 3, 0); inst.addWidget(self.rpm, 3, 1)
        root.addLayout(inst)
        self.setCentralWidget(central)

        # SSF menu: same actions through the same gate [a: Behaviour B12]
        menu = self.menuBar().addMenu("SSF")
        self.ssf_actions = {}
        for key, text in SSF_BUTTONS:
            a = menu.addAction(text)
            a.triggered.connect(lambda _, k=key: self.ssf_requested.emit(k))
            self.ssf_actions[key] = a

        self.set_system_state(dict(park_brake=True, engine_state="OFF", air_brake=False, gear_down=True))
        self.set_running(False)

    # -------------------------------------------------------------- updates
    def log(self, text):
        self.messages.appendPlainText(text)

    def set_running(self, running):
        """Freeze gate: freeze-only controls enabled only while frozen."""
        self.lbl_state.setText("SIM: RUNNING" if running else "SIM: FROZEN")
        for k in FREEZE_GATED:
            self.ssf_buttons[k].setEnabled(not running)
            self.ssf_actions[k].setEnabled(not running)
        self.cmb_airport.setEnabled(not running)  # [a: L2SYS-REQ-01.04b]
        self.ssf_buttons["run"].setEnabled(not running)
        self.ssf_buttons["pause"].setEnabled(running)

    def set_system_state(self, t):
        pb, es, ab, gd = t["park_brake"], t["engine_state"], t["air_brake"], t["gear_down"]
        self.btn_prkbrk.setText("PRK BRK: ON" if pb else "PRK BRK: OFF")
        _style(self.btn_prkbrk, "#ff8c00", "#fff") if pb else _style(self.btn_prkbrk, "#000", "#00e03c", "#666")
        self.btn_engine.setText({"OFF": "ENGINE: OFF", "STARTING": "ENGINE: START", "RUNNING": "ENGINE: ON"}[es])
        _style(self.btn_engine, {"OFF": "#d02020", "STARTING": "#d0b000", "RUNNING": "#10a040"}[es], "#fff")
        self.btn_airbrake.setText("AIR BRAKE: ON" if ab else "AIR BRAKE: OFF")
        _style(self.btn_airbrake, "#d0b000", "#000") if ab else _style(self.btn_airbrake, "#000", "#00e03c", "#666")
        self.btn_gear.setText("GEAR: DOWN" if gd else "GEAR: UP")
        _style(self.btn_gear, "#10a040", "#fff") if gd else _style(self.btn_gear, "#000", "#00e03c", "#666")

    def init_fields(self, t):
        """Navigation fields start at the current state (screenshot: Alt 454, Hdg 82)."""
        for key, val in (("alt", t["alt_m"]), ("spd", t["speed_mps"]), ("hdg", t["heading_deg"] % 360)):
            f = self.fields[key]
            f.blockSignals(True); f.setValue(round(val)); f.blockSignals(False)

    def update_cockpit_displays(self, t):
        """updateCockpitDisplays."""
        self.adi.set_attitude(t["pitch_deg"], t["roll_deg"])
        self.hdg.set_heading(t["heading_deg"])
        self.asi.set_value(t["speed_mps"])
        self.alt.set_value(t["alt_m"])
        self.fuel.set_value(t["fuel_lbs"])
        self.rpm.set_value(t["n2_pct"])
        self.hud.set_state(t["pitch_deg"], t["roll_deg"], t["heading_deg"], t["speed_mps"], t["alt_m"])
        self.set_system_state(t)
        for key, val in (("lat", t["lat_deg"]), ("lon", t["lon_deg"])):
            if not self.fields[key].hasFocus():
                self.fields[key].setValue(val)
        for mode, btn, key, tkey in (("ap_engaged", self.btn_ap, "hdg", "target_heading_deg"),
                                     ("ah_engaged", self.btn_ah, "alt", "target_alt_m"),
                                     ("at_engaged", self.btn_at, "spd", "target_speed_mps")):
            if btn.isChecked() != t[mode]:
                btn.blockSignals(True); btn.setChecked(t[mode]); btn.blockSignals(False)
            if t[mode] and not self.fields[key].hasFocus():
                f = self.fields[key]
                f.blockSignals(True); f.setValue(t[tkey] % 360 if key == "hdg" else t[tkey]); f.blockSignals(False)

    def update_attention_getters(self, warning):
        """updateAttentionGetters [a: L2SYS-REQ-02.04]."""
        self.attention.set_warning(bool(warning))
