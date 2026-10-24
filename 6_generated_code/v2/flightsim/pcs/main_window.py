"""UserInterfaceManager -- the Pilot Control Station window.
[a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml]

[a: L2SYS-REQ-01.08] a virtual cockpit console for the pilot.
[a: L2SYS-REQ-01.06] a System Controls Panel.
[a: L2SYS-REQ-01.06b/c/d/e] ENGINE / PARK BRAKE / AIR BRAKE / LDG GEAR toggles.
[a: L2SYS-REQ-01.08b] on-screen virtual flight controls with physical precedence.
[a: L2SYS-REQ-01.08c] gauges: Airspeed, Attitude, Fuel, Heading, RPM, HUD view,
    Attention Getters, Message Window.
[a: L2SYS-REQ-01.10] a Sim Control Panel for the selection of SSF.
[a: L2SYS-REQ-01.10b] the nine SSF, per ARINC 610.
[a: L2SYS-REQ-01.11] a Navigation Controls Panel with AP / AH / AT.
[a: L2SYS-REQ-01.04b] the airport selection is changeable only during
    flight-freeze.
[a: L2SYS-REQ-02.03] receive threat detections from the Synthetic Environment.
[a: L2SYS-REQ-02.04] a ground-proximity attention getter, flashing.

[a: D14] gives the layout, which follows `Desired_UIUX_capture.JPG`
[a: Sec.4.3, authoritative for layout]: attention-getter strip; tabulated
Ownship State readouts (fixed-width, right-aligned); Virtual HOTAS on the left;
System Controls (state-coloured); Navigation Controls; Mission Control
(freeze-gated airport combo); the Sim Control SSF button grid; the gauge column;
the message log box; dark QSS. "The SSF grid and the SSF menu command identical
behaviour" -- realized structurally: both are built from the one
`SSF_COMMANDS` table and call the same bound method, so they cannot diverge.

*LAUNCH 3D OTW* is the only control that may depend on FlightGear.
[a: Sec.3 criterion 6] [a: R-8] With none installed it reports which variable to
set and leaves the simulation running; it never raises and never exits.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable

from flightsim.common import config, ports
from flightsim.common.messages import OwnshipState
from flightsim.pcs import telemetry as telemetry_mod
from flightsim.pcs.airports import DEFAULT_ICAO, Airport, find, load_airports
from flightsim.pcs.autopilot import Autopilot
from flightsim.pcs.controls import ControlSample
from flightsim.pcs.io_manager import IOManager
from flightsim.pcs.map_window import ThreadMapWindow
from flightsim.pcs.physics import ControlCommand, PhysicsThread, Snapshot
from flightsim.pcs.virtual_hotas import build_virtual_hotas
from flightsim.pcs.warnings import AttentionGetter

# ---------------------------------------------------------------------------
# The nine SSF. [a: L2SYS-REQ-01.10b]
#
#   (label, method name, freeze_gated)
#
# `freeze_gated` marks the ones whose caveat in
# fsim_TA_PCS_ThreadFlightSim_UIM.sysml reads "can be applied only during a
# flight-freeze condition, i.e. after a ssfStopSimulation has been executed":
# ssfResetSimulation, ssfSetOwnPosition, ssfSaveSnapshot, ssfLoadSnapshot and
# ssfSelectAirport (the last also being L2SYS-REQ-01.04b).
# ---------------------------------------------------------------------------
SSF_COMMANDS: tuple[tuple[str, str, bool], ...] = (
    ("RUN SIM", "ssf_run", False),
    ("PAUSE SIM", "ssf_pause", False),
    ("RESET SIM", "ssf_reset", True),
    ("LAUNCH 3D OTW", "ssf_launch_otw", False),
    ("SET POSITION", "ssf_set_position", True),
    ("Save Snapshot", "ssf_save_snapshot", True),
    ("Load Snapshot", "ssf_load_snapshot", True),
    ("FUEL FREEZE", "ssf_fuel_freeze", False),
    ("Telemetry Capture", "ssf_telemetry_capture", False),
)

#: [a: D14] "dark QSS", following the reference screenshot.
PCS_QSS = """
QWidget { background-color: #1e1e1e; color: #d4d4d4; font-size: 11px; }
QMainWindow { background-color: #1e1e1e; }
QGroupBox { border: 1px solid #3c3c3c; margin-top: 8px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #9cdcfe; }
QPushButton { background-color: #3c3c3c; border: 1px solid #555; padding: 5px; }
QPushButton:hover { background-color: #4a4a4a; }
QPushButton:disabled { color: #6a6a6a; border-color: #333; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
    background-color: #2d2d2d; border: 1px solid #3c3c3c; padding: 3px;
}
QLabel#attentionGetter { color: #ff3b30; font-weight: bold; font-size: 15px; }
"""

#: [a: D14] the System Controls buttons are "state-coloured".
STATE_COLOURS = {
    "on": "#e07b00",        # PRK BRK: ON, amber in the screenshot
    "off": "#3c3c3c",
    "danger": "#c62828",    # ENGINE: OFF, red in the screenshot
    "good": "#2e7d32",      # GEAR: DOWN, green in the screenshot
}

#: [a: D14] "gauge column (Airspeed, Attitude, Altitude, Fuel, Heading, RPM,
#: HUD line) per 01.08c"
GAUGE_NAMES = ("Attitude", "Heading", "Airspeed", "Altitude", "Fuel", "RPM",
               "HUD")

MPS_TO_KTS = 1.9438444924406046
M_TO_FT = 3.280839895013123


@dataclass
class PcsPaths:
    """Every directory the PCS writes to, as parameters. [a: D18]

    "Every path the application writes to by default is a parameter the suite
    can redirect."
    """

    telemetry_dir: str
    snapshot_dir: str
    tile_cache_dir: str
    airports_dir: str | None = None


def default_paths(base: str | None = None) -> PcsPaths:
    root = base or os.path.join(os.path.expanduser("~"), ".flightsim")
    return PcsPaths(
        telemetry_dir=os.path.join(root, "telemetry"),
        snapshot_dir=os.path.join(root, "snapshots"),
        tile_cache_dir=os.path.join(root, "tiles"),
        airports_dir=root,
    )


def build_main_window(
    paths: PcsPaths,
    physics: PhysicsThread | None = None,
    io: IOManager | None = None,
    flightgear: config.FlightGearConfig | None = None,
    clock: Callable[[], float] | None = None,
    launcher: Callable[[list[str]], object] | None = None,
    tile_fetch: Callable[[str], bytes | None] | None = None,
    tile_origin: str = ports.OSM_TILE_ORIGIN,
    airports: tuple[Airport, ...] | None = None,
):
    """Construct the PCS window. Qt is imported here only."""
    from PyQt5 import QtCore, QtGui, QtWidgets

    class Gauge(QtWidgets.QGroupBox):
        """One instrument. [a: L2SYS-REQ-01.08c] [a: D14]"""

        def __init__(self, name: str, unit: str = "", second_unit: str = ""):
            super().__init__(name)
            self.setObjectName(f"gauge{name}")
            self.gauge_name = name
            self.unit = unit
            self.second_unit = second_unit
            self.value = 0.0
            self.second_value = 0.0
            layout = QtWidgets.QVBoxLayout(self)
            self.primary = QtWidgets.QLabel("0")
            self.primary.setObjectName(f"gauge{name}Value")
            self.primary.setAlignment(QtCore.Qt.AlignCenter)
            self.primary.setStyleSheet(
                "color: #00ff66; font-family: monospace; font-size: 14px;"
            )
            layout.addWidget(self.primary)
            self.secondary = QtWidgets.QLabel("")
            self.secondary.setObjectName(f"gauge{name}Second")
            self.secondary.setAlignment(QtCore.Qt.AlignCenter)
            self.secondary.setStyleSheet("color: #7fbf9f;")
            layout.addWidget(self.secondary)

        def set_value(self, value: float, second: float | None = None) -> None:
            self.value = float(value)
            self.primary.setText(f"{value:,.1f} {self.unit}".strip())
            if second is not None:
                self.second_value = float(second)
                self.secondary.setText(f"{second:,.0f} {self.second_unit}"
                                       .strip())

    class MainWindow(QtWidgets.QMainWindow):
        """threadFlightSim's UI manager. [a: fsim_TA_PCS_ThreadFlightSim.sysml]"""

        def __init__(self):
            super().__init__()
            import time

            self._clock = clock or time.monotonic
            self.paths = paths
            self.airports = airports if airports is not None else load_airports(
                directory=paths.airports_dir
            )
            self.physics = physics or PhysicsThread(
                airport=find(self.airports, DEFAULT_ICAO) or self.airports[0],
                clock=self._clock,
            )
            self.io = io if io is not None else IOManager(clock=self._clock)
            self.autopilot = Autopilot()
            self.attention = AttentionGetter(clock=self._clock)
            self.flightgear = flightgear or config.FlightGearConfig(
                executable=config.FGFS_PROGRAM_NAME, resolved=False,
                source=config.SOURCE_UNRESOLVED,
            )
            self._launcher = launcher
            self.capture = telemetry_mod.TelemetryCapture(paths.telemetry_dir)
            self.map = ThreadMapWindow(paths.tile_cache_dir, fetch=tile_fetch,
                                       origin=tile_origin)
            self.last_state: OwnshipState | None = None
            self.launched_commands: list[list[str]] = []
            self._timer = None

            self.setWindowTitle("F-16 PCS (Pilot Control Station)")
            self.setStyleSheet(PCS_QSS)          # [a: D14] dark QSS
            self._build()
            self.physics.initialize()
            self._on_state(self.physics.telemetry.get())
            self._sync_freeze_gates()

        # ==============================================================
        # Layout [a: D14]
        # ==============================================================
        def _build(self) -> None:
            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            outer = QtWidgets.QVBoxLayout(central)

            outer.addWidget(self._build_attention_strip())

            middle = QtWidgets.QHBoxLayout()
            middle.addWidget(self._build_virtual_hotas())
            column = QtWidgets.QVBoxLayout()
            column.addWidget(self._build_system_controls())
            column.addWidget(self._build_navigation_controls())
            middle.addLayout(column)
            middle.addWidget(self._build_mission_control())
            middle.addWidget(self._build_gauges())
            outer.addLayout(middle)

            outer.addWidget(self._build_sim_control())
            outer.addWidget(self._build_message_log())
            self._build_menu()

        # -- attention-getter strip ---------------------------------
        def _build_attention_strip(self):
            box = QtWidgets.QWidget()
            box.setObjectName("attentionStrip")
            row = QtWidgets.QHBoxLayout(box)
            self.attention_left = QtWidgets.QLabel("")
            self.attention_left.setObjectName("attentionLeft")
            self.attention_label = QtWidgets.QLabel("")
            self.attention_label.setObjectName("attentionGetter")
            self.attention_label.setAlignment(QtCore.Qt.AlignCenter)
            self.attention_right = QtWidgets.QLabel("")
            self.attention_right.setObjectName("attentionRight")
            for w in (self.attention_left, self.attention_label,
                      self.attention_right):
                w.setMinimumHeight(24)
            row.addWidget(self.attention_left, 0)
            row.addWidget(self.attention_label, 1)
            row.addWidget(self.attention_right, 0)
            return box

        # -- virtual HOTAS ------------------------------------------
        def _build_virtual_hotas(self):
            self.virtual_hotas = build_virtual_hotas(
                on_change=self._on_virtual_change
            )
            return self.virtual_hotas

        # -- System Controls [a: 01.06] -----------------------------
        def _build_system_controls(self):
            box = QtWidgets.QGroupBox("System Controls")
            box.setObjectName("systemControls")
            grid = QtWidgets.QGridLayout(box)

            self.btn_park_brake = self._state_button(
                "prkBrk", "PRK BRK", self.toggle_park_brake)
            self.btn_engine = self._state_button(
                "engine", "ENGINE", self.toggle_engine)
            self.btn_air_brake = self._state_button(
                "airBrake", "AIR BRAKE", self.toggle_air_brake)
            self.btn_gear = self._state_button(
                "gear", "GEAR", self.toggle_gear)

            grid.addWidget(self.btn_park_brake, 0, 0)
            grid.addWidget(self.btn_engine, 0, 1)
            grid.addWidget(self.btn_air_brake, 1, 0)
            grid.addWidget(self.btn_gear, 1, 1)
            return box

        def _state_button(self, name, label, slot):
            b = QtWidgets.QPushButton(label)
            b.setObjectName(name)
            b.clicked.connect(slot)
            return b

        # -- Navigation Controls [a: 01.11] -------------------------
        def _build_navigation_controls(self):
            box = QtWidgets.QGroupBox("Navigation Controls")
            box.setObjectName("navigationControls")
            grid = QtWidgets.QGridLayout(box)

            self.target_alt = self._target_row(grid, 0, "Alt", "targetAlt",
                                               -500.0, 30000.0, 1.0)
            self.target_spd = self._target_row(grid, 1, "Spd", "targetSpd",
                                               0.0, 900.0, 1.0)
            self.target_hdg = self._target_row(grid, 2, "Hdg", "targetHdg",
                                               0.0, 359.9, 1.0)

            self.field_lat = QtWidgets.QLineEdit("0.0")
            self.field_lat.setObjectName("fieldLat")
            self.field_lon = QtWidgets.QLineEdit("0.0")
            self.field_lon.setObjectName("fieldLon")
            grid.addWidget(QtWidgets.QLabel("Lat"), 3, 0)
            grid.addWidget(self.field_lat, 3, 1, 1, 3)
            grid.addWidget(QtWidgets.QLabel("Lon"), 4, 0)
            grid.addWidget(self.field_lon, 4, 1, 1, 3)

            self.btn_ap = self._mode_button("AP", self.toggle_ap)
            self.btn_ah = self._mode_button("AH", self.toggle_ah)
            self.btn_at = self._mode_button("AT", self.toggle_at)
            grid.addWidget(self.btn_ap, 5, 0)
            grid.addWidget(self.btn_ah, 5, 1)
            grid.addWidget(self.btn_at, 5, 2)

            # [a: 01.10b] SET POSITION takes Altitude, Speed and Heading from
            # the same three fields, plus Lat and Lon.
            return box

        def _target_row(self, grid, row, label, name, lo, hi, step):
            minus = QtWidgets.QPushButton("-")
            minus.setObjectName(f"{name}Minus")
            spin = QtWidgets.QDoubleSpinBox()
            spin.setObjectName(name)
            spin.setRange(lo, hi)
            spin.setDecimals(1)
            spin.setSingleStep(step)
            plus = QtWidgets.QPushButton("+")
            plus.setObjectName(f"{name}Plus")
            minus.clicked.connect(spin.stepDown)
            plus.clicked.connect(spin.stepUp)
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            grid.addWidget(minus, row, 1)
            grid.addWidget(spin, row, 2)
            grid.addWidget(plus, row, 3)
            return spin

        def _mode_button(self, label, slot):
            b = QtWidgets.QPushButton(label)
            b.setObjectName(f"mode{label}")
            b.setCheckable(True)
            b.clicked.connect(slot)
            return b

        # -- Mission Control + Ownship State readouts [a: D14] ------
        def _build_mission_control(self):
            box = QtWidgets.QGroupBox("Mission Control")
            box.setObjectName("missionControl")
            layout = QtWidgets.QVBoxLayout(box)
            layout.addWidget(QtWidgets.QLabel("Location:"))
            self.airport_combo = QtWidgets.QComboBox()
            self.airport_combo.setObjectName("airportCombo")
            for a in self.airports:
                self.airport_combo.addItem(a.label(), a.icao)
            self.airport_combo.currentIndexChanged.connect(
                self._on_airport_changed
            )
            layout.addWidget(self.airport_combo)

            # [a: D14] "tabulated Ownship State readouts (fixed-width,
            # right-aligned)". Reported as F-4: the reference screenshot shows
            # no such panel; it goes in the empty area under the Location combo,
            # which satisfies D14 without displacing anything the screenshot
            # shows.
            self.state_table = QtWidgets.QTableWidget(0, 2)
            self.state_table.setObjectName("ownshipStateTable")
            self.state_table.horizontalHeader().setVisible(False)
            self.state_table.verticalHeader().setVisible(False)
            self.state_table.setEditTriggers(
                QtWidgets.QAbstractItemView.NoEditTriggers
            )
            self._state_rows = (
                "sim_time_s", "lat_deg", "lon_deg", "alt_m", "agl_m",
                "heading_deg", "pitch_deg", "roll_deg", "speed_mps",
                "vspeed_mps", "rpm_pct", "fuel_lbs",
            )
            self.state_table.setRowCount(len(self._state_rows))
            mono = QtGui.QFont("monospace")
            for i, name in enumerate(self._state_rows):
                key = QtWidgets.QTableWidgetItem(name)
                key.setFont(mono)
                value = QtWidgets.QTableWidgetItem("0.0")
                value.setFont(mono)
                value.setTextAlignment(QtCore.Qt.AlignRight
                                       | QtCore.Qt.AlignVCenter)
                self.state_table.setItem(i, 0, key)
                self.state_table.setItem(i, 1, value)
            layout.addWidget(self.state_table)
            return box

        # -- gauge column [a: 01.08c] --------------------------------
        def _build_gauges(self):
            box = QtWidgets.QGroupBox("Instruments")
            box.setObjectName("gaugeColumn")
            layout = QtWidgets.QVBoxLayout(box)
            self.gauges: dict[str, Gauge] = {}
            specs = (
                ("Attitude", "deg", "roll"),
                ("Heading", "deg", ""),
                ("Airspeed", "m/s", "kts"),
                ("Altitude", "m", "ft"),
                ("Fuel", "lbs", ""),
                ("RPM", "%", ""),
            )
            for name, unit, second in specs:
                g = Gauge(name, unit, second)
                self.gauges[name] = g
                layout.addWidget(g)
            # [a: 01.08c] "HUD view"
            self.hud = QtWidgets.QLabel("")
            self.hud.setObjectName("gaugeHUD")
            self.hud.setStyleSheet(
                "color: #00ff66; font-family: monospace; font-size: 12px;"
            )
            layout.addWidget(self.hud)
            return box

        # -- Sim Control grid [a: 01.10] -----------------------------
        def _build_sim_control(self):
            box = QtWidgets.QGroupBox("Sim Control")
            box.setObjectName("simControl")
            grid = QtWidgets.QGridLayout(box)
            self.ssf_buttons: dict[str, object] = {}
            for i, (label, method, _gated) in enumerate(SSF_COMMANDS):
                b = QtWidgets.QPushButton(label)
                b.setObjectName(f"ssf_{method}")
                # the SAME bound method the menu item gets [a: D14]
                b.clicked.connect(getattr(self, method))
                grid.addWidget(b, i // 5, i % 5)
                self.ssf_buttons[method] = b
            return box

        def _build_menu(self) -> None:
            """[a: D14] "The SSF grid and the SSF menu command identical
            behaviour." Both are built from SSF_COMMANDS and bind the same
            method, so they cannot diverge."""
            menu = self.menuBar().addMenu("SSF")
            self.ssf_actions: dict[str, object] = {}
            for label, method, _gated in SSF_COMMANDS:
                action = menu.addAction(label)
                action.setObjectName(f"action_{method}")
                action.triggered.connect(getattr(self, method))
                self.ssf_actions[method] = action

        # -- message log [a: 01.08c] "Message Window" ----------------
        def _build_message_log(self):
            self.message_log = QtWidgets.QPlainTextEdit()
            self.message_log.setObjectName("messageLog")
            self.message_log.setReadOnly(True)
            self.message_log.setMaximumHeight(90)
            return self.message_log

        # ==============================================================
        # Messages
        # ==============================================================
        def log(self, text: str) -> None:
            self.message_log.appendPlainText(text)

        @property
        def log_text(self) -> str:
            return self.message_log.toPlainText()

        # ==============================================================
        # System Controls [a: 01.06b-e]
        # ==============================================================
        def _command(self, **kw) -> ControlCommand:
            return self.physics.commands.update(**kw)

        def _apply_now(self) -> None:
            """Push the new command to the aircraft immediately.

            The System Controls are usable while flight-frozen, so a toggle has
            to reach the FDM and the readouts without waiting for the next
            120 Hz frame -- which, paused, never comes.
            [a: L2SYS-REQ-01.06b-e]
            """
            state = self.physics.apply_commands_now()
            self.last_state = state
            self._refresh_gauges(state)
            self._refresh_state_table(state)
            for message in self.physics.drain_messages():
                self.log(message)

        def toggle_engine(self) -> None:
            """[a: L2SYS-REQ-01.06b] ENGINE toggle-button (ON/OFF)."""
            current = self.physics.commands.get().engine_on
            self._command(engine_on=not current)
            self._apply_now()
            self._refresh_system_controls()

        def toggle_park_brake(self) -> None:
            """[a: L2SYS-REQ-01.06c] PARK BRAKE toggle-button (ON/OFF)."""
            self._command(parking_brake=not
                          self.physics.commands.get().parking_brake)
            self._apply_now()
            self._refresh_system_controls()

        def toggle_air_brake(self) -> None:
            """[a: L2SYS-REQ-01.06d] AIR BRAKE toggle-button (ON/OFF)."""
            self._command(air_brake=not self.physics.commands.get().air_brake)
            self._apply_now()
            self._refresh_system_controls()

        def toggle_gear(self) -> None:
            """[a: L2SYS-REQ-01.06e] LDG GEAR toggle-button (UP/DOWN)."""
            self._command(gear_down=not self.physics.commands.get().gear_down)
            self._apply_now()
            self._refresh_system_controls()

        def _refresh_system_controls(self) -> None:
            """[a: D14] "state-coloured PRK BRK / ENGINE / AIR BRAKE / GEAR"."""
            c = self.physics.commands.get()
            self._set_state(self.btn_park_brake, "PRK BRK",
                            "ON" if c.parking_brake else "OFF",
                            "on" if c.parking_brake else "off")
            self._set_state(self.btn_engine, "ENGINE",
                            "ON" if c.engine_on else "OFF",
                            "good" if c.engine_on else "danger")
            self._set_state(self.btn_air_brake, "AIR BRAKE",
                            "ON" if c.air_brake else "OFF",
                            "on" if c.air_brake else "off")
            self._set_state(self.btn_gear, "GEAR",
                            "DOWN" if c.gear_down else "UP",
                            "good" if c.gear_down else "on")

        def _set_state(self, button, label, state, colour_key) -> None:
            button.setText(f"{label}: {state}")
            colour = STATE_COLOURS[colour_key]
            button.setStyleSheet(
                f"background-color: {colour}; color: #ffffff;"
                f" border: 1px solid #555; padding: 5px;"
            )

        # ==============================================================
        # Navigation Controls [a: 01.11]
        # ==============================================================
        def toggle_ah(self) -> None:
            """[a: D11] "Engaging AP, AH or AT captures the current value as
            target.\""""
            if self.autopilot.modes.ah:
                self.autopilot.disengage_ah()
                self.log("AH disengaged")
            else:
                current = self.last_state.alt_m if self.last_state else 0.0
                self.autopilot.engage_ah(current)
                self.target_alt.setValue(current)
                self.log(f"AH engaged, target {current:.0f} m")
            self._sync_mode_buttons()

        def toggle_ap(self) -> None:
            if self.autopilot.modes.ap:
                self.autopilot.disengage_ap()
                self.log("AP disengaged")
            else:
                current = self.last_state.heading_deg if self.last_state else 0.0
                self.autopilot.engage_ap(current)
                self.target_hdg.setValue(current)
                self.log(f"AP engaged, target {current:.0f} deg")
            self._sync_mode_buttons()

        def toggle_at(self) -> None:
            if self.autopilot.modes.at:
                self.autopilot.disengage_at()
                self.log("AT disengaged")
            else:
                current = self.last_state.speed_mps if self.last_state else 0.0
                self.autopilot.engage_at(current)
                self.target_spd.setValue(current)
                self.log(f"AT engaged, target {current:.0f} m/s")
            self._sync_mode_buttons()

        def _sync_mode_buttons(self) -> None:
            self.btn_ap.setChecked(self.autopilot.modes.ap)
            self.btn_ah.setChecked(self.autopilot.modes.ah)
            self.btn_at.setChecked(self.autopilot.modes.at)

        # ==============================================================
        # SSF [a: 01.10b]
        # ==============================================================
        def _require_freeze(self, what: str) -> bool:
            """The UIM caveat: "can be applied only during a flight-freeze
            condition". [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml]
            [a: L2SYS-REQ-01.04b] for the airport."""
            if self.physics.running:
                self.log(f"{what} requires a flight freeze: PAUSE SIM first")
                return False
            return True

        def ssf_run(self) -> None:
            """RUN SIM: start/resume simulation. [a: 01.10b]"""
            self.physics.ssf_run()
            self.log("RUN SIM")
            self._sync_freeze_gates()

        def ssf_pause(self) -> None:
            """PAUSE SIM: pause simulation (flight-freeze). [a: 01.10b]"""
            self.physics.ssf_pause()
            self.log("PAUSE SIM (flight freeze)")
            self._sync_freeze_gates()

        def ssf_reset(self) -> None:
            """RESET SIM: reset simulation to initial state (t0). [a: 01.10b]

            [a: D8] "Reset returns the clean state -- autopilot and fuel freeze
            disengaged -- and publishes one telemetry frame and one FlightGear
            frame even while flight-frozen."
            """
            if not self._require_freeze("RESET SIM"):
                return
            icao = self.airport_combo.currentData()
            state = self.physics.ssf_reset(find(self.airports, icao))
            self.autopilot.disengage_all()          # [a: D8]
            self._sync_mode_buttons()
            self.attention.reset()
            self.io.publish(state, force=True)      # [a: D8] one frame, frozen
            self._on_state(state)
            self.log("RESET SIM: initial conditions restored")

        def ssf_launch_otw(self) -> None:
            """LAUNCH 3D OTW: launch FlightGear. [a: 01.10b] [a: D6] [a: D16]

            The only control that may depend on FlightGear. With none
            installed it reports which variable to set and leaves the
            simulation running; it never raises and never exits.
            [a: Sec.3 criterion 6] [a: R-8]
            """
            for warning in self.flightgear.warnings:
                self.log(warning)
            if not self.flightgear.resolved:
                self.log(self.flightgear.missing_variable_hint)
                return
            icao = self.airport_combo.currentData() or DEFAULT_ICAO
            argv = config.build_fgfs_command(
                self.flightgear, icao=icao,
                fdm_port=ports.PCS_TO_FLIGHTGEAR_FDM,
            )
            self.launched_commands.append(argv)
            self.log(f"LAUNCHING FLIGHTGEAR 3D ENGINE AT {icao}...")
            self.log("Waiting for visual database...")
            try:
                launch = self._launcher or _default_launcher
                launch(argv)
            except Exception as exc:                # never raises [a: R-8]
                self.log(f"FlightGear could not be started: {exc}")
                return
            # [a: D9] once FlightGear is up, the terrain poll has a source
            self.io.ground_elev_url = ports.ground_elev_url()
            self.physics.ground_elev_source = self.io.poll_ground_elevation

        def ssf_set_position(self) -> None:
            """SET POSITION: Lat, Lon (deg), Altitude (m), Speed (m/s),
            Heading (deg). [a: 01.10b] [a: D8] [a: D20]"""
            if not self._require_freeze("SET POSITION"):
                return
            try:
                lat = float(self.field_lat.text())
                lon = float(self.field_lon.text())
            except ValueError:
                self.log("SET POSITION: Lat and Lon must be numbers")
                return
            self.physics.ssf_set_position(
                lat_deg=lat, lon_deg=lon,
                alt_m=self.target_alt.value(),
                speed_mps=self.target_spd.value(),
                heading_deg=self.target_hdg.value(),
            )
            self._on_state(self.physics.telemetry.get())
            self.log(f"SET POSITION: {lat:.4f}, {lon:.4f}, "
                     f"{self.target_alt.value():.0f} m, "
                     f"{self.target_spd.value():.0f} m/s, "
                     f"{self.target_hdg.value():.0f} deg")

        def ssf_save_snapshot(self, path: str | None = None) -> str | None:
            """SAVE SNAPSHOT: save full simulation state. [a: 01.10b]"""
            if not self._require_freeze("SAVE SNAPSHOT"):
                return None
            snap = self.physics.save_snapshot()
            payload = {
                "physics": snap.__dict__,
                "autopilot": {
                    "targets": self.autopilot.targets.__dict__,
                    "modes": self.autopilot.modes.__dict__,
                },
                "airport": self.airport_combo.currentData(),
                "map": {"zoom": self.map.mapGuiWorker.zoom},
            }
            target = path or os.path.join(self.paths.snapshot_dir,
                                          "snapshot.json")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            self.log(f"SAVE SNAPSHOT -> {target}")
            return target

        def ssf_load_snapshot(self, path: str | None = None) -> bool:
            """LOAD SNAPSHOT: restore full simulation state. [a: 01.10b]"""
            if not self._require_freeze("LOAD SNAPSHOT"):
                return False
            target = path or os.path.join(self.paths.snapshot_dir,
                                          "snapshot.json")
            try:
                with open(target, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, ValueError) as exc:
                self.log(f"LOAD SNAPSHOT failed: {exc}")
                return False
            snap = Snapshot(**payload["physics"])
            state = self.physics.load_snapshot(snap)
            targets = payload.get("autopilot", {}).get("targets", {})
            self.autopilot.set_targets(**targets)
            modes = payload.get("autopilot", {}).get("modes", {})
            self.autopilot.disengage_all()
            if modes.get("ah"):
                self.autopilot.engage_ah(self.autopilot.targets.altitude_m)
            if modes.get("ap"):
                self.autopilot.engage_ap(self.autopilot.targets.heading_deg)
            if modes.get("at"):
                self.autopilot.engage_at(self.autopilot.targets.speed_mps)
            self._sync_mode_buttons()
            zoom = payload.get("map", {}).get("zoom")
            if zoom is not None:
                self.map.mapGuiWorker.zoom = int(zoom)
            self._on_state(state)
            self.log(f"LOAD SNAPSHOT <- {target}")
            return True

        def ssf_fuel_freeze(self) -> None:
            """FUEL FREEZE: hold tank contents constant. [a: 01.10b]"""
            engaged = not self.physics.fuel_freeze
            self.physics.ssf_fuel_freeze(engaged)
            self.log(f"FUEL FREEZE {'engaged' if engaged else 'released'}")

        def ssf_telemetry_capture(self) -> None:
            """TELEMETRY CAPTURE: log telemetry to CSV file. [a: 01.10b]
            [a: D14] the 28-field header."""
            if self.capture.engaged:
                path = self.capture.stop()
                self.log(f"TELEMETRY CAPTURE stopped -> {path}")
            else:
                path = self.capture.toggle(self.paths.telemetry_dir)
                self.log(f"TELEMETRY CAPTURE started -> {self.capture.path}")
                _ = path

        def _sync_freeze_gates(self) -> None:
            """[a: L2SYS-REQ-01.04b] "The airport selection shall be changeable
            only during flight-freeze."""
            frozen = not self.physics.running
            self.airport_combo.setEnabled(frozen)
            for _label, method, gated in SSF_COMMANDS:
                if gated:
                    self.ssf_buttons[method].setEnabled(frozen)
                    self.ssf_actions[method].setEnabled(frozen)

        def _on_airport_changed(self, _index: int) -> None:
            if self.physics.running:
                return                        # gated; the combo is disabled
            icao = self.airport_combo.currentData()
            airport = find(self.airports, icao)
            if airport is None:
                return
            self.physics.initialize(airport)
            self._on_state(self.physics.telemetry.get())
            self.log(f"Airport set to {airport.label()}")

        # ==============================================================
        # The periodic pass
        # ==============================================================
        def _on_virtual_change(self, sample: ControlSample) -> None:
            self.io.arbiter.on_virtual(sample)

        def tick(self) -> None:
            """One UI pass: drain I/O, apply arbitration and the autopilot,
            step the physics if running, and refresh the displays."""
            self.io.tick()

            # [a: D10] the widget greys out and its title changes while the
            # physical device holds precedence, and mirrors it at 10 Hz.
            locked = self.io.physical_active
            self.virtual_hotas.set_locked(locked)
            if locked and self.io.mirror_timer.due():
                self.virtual_hotas.mirror(self.io.arbiter.physical_sample)

            sample = self.io.resolve_controls(
                None if locked else self.virtual_hotas.sample()
            )
            toggles = self.io.toggles
            command_kw = dict(
                roll=sample.roll, pitch=sample.pitch, yaw=sample.yaw,
                throttle=sample.throttle,
                wheel_brake=max(self.io.wheel_brake,
                                self.virtual_hotas.wheel_brake),
            )
            if self.io.hotas_rx is not None and self.io.hotas_rx.received:
                # the receiver-side toggles only apply once a physical device
                # has actually spoken [a: D7]
                command_kw["gear_down"] = toggles.gear_down
                command_kw["air_brake"] = toggles.air_brake

            # [a: D11] the autopilot overwrites only the axes its engaged modes
            # own; an unengaged mode writes nothing.
            if self.last_state is not None:
                auto = self.autopilot.compute(
                    alt_m=self.last_state.alt_m,
                    vspeed_mps=self.last_state.vspeed_mps,
                    pitch_deg=self.last_state.pitch_deg,
                    heading_deg=self.last_state.heading_deg,
                    roll_deg=self.last_state.roll_deg,
                    speed_mps=self.last_state.speed_mps,
                )
                command_kw.update(auto)
            self.physics.commands.update(**command_kw)

            if self.physics.running:
                state = self.physics.step()
                self.io.publish(state)
                self._on_state(state)
            else:
                # Frozen: the aircraft does not move, but incoming threat
                # detections still have to reach the map, or a beacon injected
                # during a freeze would be invisible until the sim resumed.
                # [a: L2SYS-REQ-02.03] [a: L2SYS-REQ-02.03b]
                self.map.set_beacons(self.io.roster)

            for message in self.physics.drain_messages():
                self.log(message)

        def _on_state(self, state: OwnshipState | None) -> None:
            if state is None:
                return
            self.last_state = state
            self._refresh_gauges(state)
            self._refresh_state_table(state)
            self._refresh_system_controls()
            self._refresh_attention(state)
            self.map.set_ownship(state.lat_deg, state.lon_deg,
                                 state.heading_deg)
            self.map.set_beacons(self.io.roster)
            if self.capture.engaged:
                self.capture.write(
                    state,
                    target_alt_m=self.target_alt.value(),
                    target_speed_mps=self.target_spd.value(),
                    target_heading_deg=self.target_hdg.value(),
                    ap_engaged=self.autopilot.modes.ap,
                    ah_engaged=self.autopilot.modes.ah,
                    at_engaged=self.autopilot.modes.at,
                    warning_state=self.attention.display_text(),
                )

        def _refresh_gauges(self, s: OwnshipState) -> None:
            """[a: L2SYS-REQ-01.08c]"""
            self.gauges["Attitude"].set_value(s.pitch_deg, s.roll_deg)
            self.gauges["Heading"].set_value(s.heading_deg)
            # [a: D20] speed_mps is true airspeed; "The Airspeed gauge may
            # additionally display knots."
            self.gauges["Airspeed"].set_value(s.speed_mps,
                                              s.speed_mps * MPS_TO_KTS)
            self.gauges["Altitude"].set_value(s.alt_m, s.alt_m * M_TO_FT)
            self.gauges["Fuel"].set_value(s.fuel_lbs)
            self.gauges["RPM"].set_value(s.rpm_pct)
            self.hud.setText(
                f"HDG {s.heading_deg:05.1f}  ALT {s.alt_m:7.0f}m  "
                f"SPD {s.speed_mps:5.1f}m/s  AGL {s.agl_m:6.0f}m"
            )

        def _refresh_state_table(self, s: OwnshipState) -> None:
            """[a: D14] "tabulated Ownship State readouts (fixed-width,
            right-aligned)\""""
            for i, name in enumerate(self._state_rows):
                self.state_table.item(i, 1).setText(
                    f"{getattr(s, name):>12.3f}"
                )

        def _refresh_attention(self, s: OwnshipState) -> None:
            """[a: L2SYS-REQ-02.04] [a: D23] [a: D14]"""
            self.attention.update(s.agl_m, s.vspeed_mps, s.on_ground)
            self.attention_label.setText(self.attention.display_text())

        # ==============================================================
        def start(self, interval_ms: int = 50):
            from PyQt5 import QtCore as C

            self._timer = C.QTimer(self)
            self._timer.setInterval(interval_ms)
            self._timer.timeout.connect(self.tick)
            self._timer.start()
            return self._timer

        def closeEvent(self, event):          # noqa: N802 - Qt naming
            if self._timer is not None:
                self._timer.stop()
            self.capture.stop()
            self.map.stop()
            self.physics.close()
            self.io.close()
            super().closeEvent(event)

    return MainWindow()


def _default_launcher(argv: list[str]):
    """Start FlightGear. The only `subprocess` call in the package. [a: D6]"""
    import subprocess

    return subprocess.Popen(argv)


def main(argv: list[str] | None = None) -> int:
    import sys

    from PyQt5 import QtWidgets

    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    paths = default_paths()
    env_text = config.read_env_file("fsim_env.bat")
    fg = config.resolve_flightgear(env_file_text=env_text)
    window = build_main_window(paths, flightgear=fg)
    for warning in fg.warnings:
        window.log(warning)
    if not fg.resolved:
        window.log("FlightGear is not configured; LAUNCH 3D OTW is unavailable.")
        window.log("Everything else runs normally.")
    window.resize(1200, 900)
    window.show()
    window.start()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
