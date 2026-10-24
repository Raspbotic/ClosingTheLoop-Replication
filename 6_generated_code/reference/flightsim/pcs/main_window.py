"""
flightsim.pcs.main_window
=========================
ThreadFlightSim: PCS main process entry -- Qt main window (Thread 1).

TRACEABILITY
------------
[a] Implements part def ThreadFlightSim (fsim_TA_PCS_ThreadFlightSim.sysml)
    = uiManager : UserInterfaceManager (..._UIM.sysml)
    + ioManager : BackgroundIOManager  (..._BkgIOM.sysml).
    TA doc names this file: "main_window.py".
[a] PCS assembly (fsim_TA_PCS.sysml): this process also instantiates
    threadPhysics (Step 3) and threadMapWindow (Step 5), and owns the
    internal bindings between them.
[a] External ports realized here (..._BkgIOM.sysml):
      e_hotasControlIn     UDP  rx 127.0.0.1:5005
      e_ownshipStateOut    UDP  tx 127.0.0.1:5006
      e_radarDetectionsIn  UDP  rx 127.0.0.1:6006
      e_groundElevMeterIn  HTTP GET 127.0.0.1:8080
[a] Internal ports (InternalSignalPort bindings in fsim_TA_PCS.sysml)
    realized as queue.Queue / Qt signals:
      i_HotasControlOut -> physics hotas queue (source = physical HOTAS
                           or the virtual HOTAS widget; physical takes
                           precedence [a: L2SYS-REQ-01.08b])
      i_OwnTelemetryIn  <- physics telemetry queue
      i_OwnStateOut, i_BeaconDataOut -> map window (Step 5 hook)
      i_2dTileIn        <- map window snapshot signal (Step 5 hook)
      i_TileCachePathOut-> map window tile cache path (Step 5 hook)

TA ACTION -> CODE MAPPING [b]
-----------------------------
  ioManager (BackgroundIOManager):
    bridgeUdpData            -> IOManager._tick rx/tx (QTimer)
    telemetryCaptureManager  -> IOManager._tick telemetry drain (QTimer)
    terrainElevationManager  -> IOManager._elevation_worker (bg thread)
  uiManager (UserInterfaceManager):
    controlUserInputs.controlManualFlight   -> HOTAS passthrough (default)
    controlUserInputs.controlFlightSystems  -> gear/brake via switches [c]
    controlUserInputs.controlMissionSettings-> SSF dialogs (position, files)
    controlUserInputs.controlNavigationAP   -> Autopilot heading hold
    controlUserInputs.controlNavigationAH   -> Autopilot altitude hold
    controlUserInputs.controlNavigationAT   -> Autopilot autothrottle
    updateCockpitDisplays    -> MainWindow._update_displays (QTimer)
    manageSSF.ssf*           -> MainWindow._ssf_* (menu bar), see below

SSF SEMANTICS [a: doc comments in ..._UIM.sysml, added 2026-07-11]
------------------------------------------------------------------
  ssfStartSimulation  start/resume loop        -> physics.ssf_start()
  ssfStopSimulation   pause loop               -> physics.ssf_stop()
  ssfResetSimulation  reload ICs; FREEZE-ONLY  -> physics.ssf_reset()
  ssfLaunchOtwView    launch FlightGear w/ aircraft type -> subprocess
  ssfSetOwnPosition   set lat/lon/alt; FREEZE-ONLY -> physics.ssf_set_own_position()
  ssfSaveSnapshot     save all state vars; FREEZE-ONLY -> JSON to disk
  ssfLoadSnapshot     restore from snapshot; FREEZE-ONLY
  ssfFreezeFuelConsupmtion  hold tank contents -> physics.ssf_freeze_fuel()
  ssfLogTelemetry     CSV log, field list verbatim from the doc comment
The FREEZE-ONLY caveat [a] is enforced by disabling those menu actions
unless the simulation is stopped (flight-freeze).

DEVELOPER CHOICES [c] (flagged inline)
--------------------------------------
- Autopilot laws: simple proportional controllers (AP: heading->roll,
  AH: altitude->pitch via vertical-speed command, AT: speed->throttle).
  Gains hand-tuned for the default c172x; adequate for a prototype,
  not certified control laws.
- Snapshot scope [a: customer decision 2026-07-11]: physics state,
  AP targets, beacon table, fuel-freeze flag. Map view and OTW camera
  are intentionally NOT serialized -- both are recoverable from the
  ownship position, per customer confirmation.
- Elevation polling at 1 Hz with 0.5 s timeout in a daemon thread
  (blocking HTTP must not run on the GUI thread).
- Beacon table: tombstone + staleness GC per common/messages.py protocol.
"""

from __future__ import annotations

import csv
import json
import queue
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QLabel, QGroupBox,
    QVBoxLayout, QHBoxLayout, QCheckBox, QDoubleSpinBox, QAction,
    QFileDialog, QMessageBox, QInputDialog, QStatusBar, QPushButton,
    QComboBox, QPlainTextEdit)

from flightsim.common import ports
from flightsim.common.messages import (
    HotasControls, OwnshipState, RadarBeacon, MessageError,
    BEACON_STALE_TIMEOUT_S)
from flightsim.pcs.physics import ThreadPhysics
from flightsim.pcs.map_window import MapWindow
from flightsim.pcs.virtual_hotas import VirtualHotas
from flightsim.pcs.physics import InitialConditions
from flightsim.pcs.airports import AIRPORTS, DEFAULT_ICAO


# ===========================================================================
# Autopilot [c] -- controlNavigationAP / AH / AT
# ===========================================================================

class Autopilot:
    """Simple P-law autopilot. Overrides HOTAS channels when engaged."""

    def __init__(self):
        self.ap_engaged = False      # heading hold
        self.ah_engaged = False      # altitude hold
        self.at_engaged = False      # autothrottle
        self.target_heading_deg = 90.0
        self.target_alt_m = 2000.0
        self.target_speed_ms = 180.0   # [b] F16 cruise

    def apply(self, c: HotasControls, tm: OwnshipState | None) -> HotasControls:
        if tm is None:
            return c
        if self.ap_engaged:                            # heading -> roll [c]
            err = (self.target_heading_deg - tm.heading_deg + 540.0) % 360.0 - 180.0
            bank_cmd = max(-25.0, min(25.0, 0.8 * err))       # deg
            c.roll = max(-1.0, min(1.0, 0.03 * (bank_cmd - tm.roll_deg)))
        if self.ah_engaged:                            # altitude -> pitch [c]
            # [b: bug fix 2026-07-13, AH damping] attitude CASCADE law:
            # alt err -> VS command -> pitch-attitude command -> elevator.
            # The attitude inner loop provides natural damping; tuned on
            # the F16 (probe 2026-07-13): off-trim engage from vs=-111
            # m/s rang 22 VS peaks with the old P-on-VS law vs 7 with
            # the cascade; smooth-step final error -33.3 m -> -15.8 m.
            vs_cmd = max(-5.0, min(5.0,
                0.15 * (self.target_alt_m - tm.alt_m)))
            theta_cmd = max(-8.0, min(12.0,
                1.5 * (vs_cmd - tm.vertical_speed_ms)))
            c.pitch = max(-1.0, min(1.0,
                0.06 * (theta_cmd - tm.pitch_deg)))
        if self.at_engaged:                            # speed -> throttle
            # [b] gain retuned on the F16 (probe test 2026-07-11):
            # 0.02 converged too slowly at jet speeds; 0.04 acceptable
            c.throttle = max(0.0, min(1.0,
                0.5 + 0.04 * (self.target_speed_ms - tm.groundspeed_ms)))
        return c


# ===========================================================================
# Telemetry CSV logger -- ssfLogTelemetry
# [a] field list VERBATIM from the ssfLogTelemetry doc comment
# ===========================================================================

TELEMETRY_FIELDS = [
    "wall_time_iso", "sim_time_s", "model", "lat_deg", "lon_deg", "alt_m",
    "agl_m", "v_speed_mps", "speed_mps", "heading_deg", "target_heading_deg",
    "pitch_deg", "pitch_rate_dps", "roll_deg", "target_alt_m",
    "target_speed_mps", "aileron_cmd", "elevator_cmd", "rudder_cmd",
    "throttle_cmd", "gear_cmd", "trim_pitch", "trim_roll", "trim_yaw",
    "ap_engaged", "ah_engaged", "at_engaged", "warning_state",
]


class TelemetryLogger:
    def __init__(self):
        self._file = None
        self._writer = None

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self, path: str) -> None:
        self._file = open(path, "w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=TELEMETRY_FIELDS)
        self._writer.writeheader()

    def stop(self) -> None:
        if self._file:
            self._file.close()
        self._file = self._writer = None

    def log(self, row: dict) -> None:
        if self._writer:
            self._writer.writerow(row)


# ===========================================================================
# BackgroundIOManager  [a: part ioManager]
# QTimer-driven non-blocking UDP + background elevation HTTP thread
# ===========================================================================

class IOManager(QObject):
    beacon_updated = pyqtSignal(object)      # RadarBeacon    -> i_BeaconDataOut
    ownship_updated = pyqtSignal(object)     # OwnshipState   -> i_OwnStateOut

    def __init__(self, hotas_out_queue: queue.Queue,
                 telemetry_in_queue: queue.Queue, autopilot: Autopilot):
        super().__init__()
        self._hotas_q = hotas_out_queue          # i_HotasControlOut
        self._telem_q = telemetry_in_queue       # i_OwnTelemetryIn
        self._autopilot = autopilot

        self.last_controls = HotasControls()      # ACTIVE source values
        self._phys_controls = HotasControls()      # last physical datagram
        self._last_phys_rx = 0.0
        self._virtual_source = None                # callable -> HotasControls
        self.physical_active = False
        self.last_telemetry: OwnshipState | None = None
        self.agl_m: float | None = None          # from e_groundElevMeterIn
        self.ground_elev_m: float | None = None  # FG scenery ground elev
        self.beacons: dict[str, tuple[RadarBeacon, float]] = {}  # id->(b, t_rx)

        # e_hotasControlIn : UDP rx 5005 [a]
        self._sock_hotas = self._udp_rx(ports.UDP_HOTAS_CONTROL)
        # e_radarDetectionsIn : UDP rx 6006 [a]
        self._sock_radar = self._udp_rx(ports.UDP_PCS_RADAR_DETECTIONS)
        # e_ownshipStateOut : UDP tx -> 5006 [a]
        self._sock_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tx_seq = 0

        # bridgeUdpData + telemetryCaptureManager [a] -- QTimer, ~60 Hz [c]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        # terrainElevationManager [a] -- daemon thread, 1 Hz [c]
        self._elev_stop = threading.Event()
        self._elev_thread = threading.Thread(
            target=self._elevation_worker, daemon=True)
        self._elev_thread.start()

    @staticmethod
    def _udp_rx(port: int) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((ports.LOCALHOST, port))
        s.setblocking(False)
        return s

    # [a: L2SYS-REQ-01.08b] physical HOTAS takes precedence when present;
    # "present" = datagram received within this window [c]
    PHYSICAL_TIMEOUT_S = 1.0

    def set_virtual_source(self, fn) -> None:
        """Register the virtual-HOTAS provider (callable -> HotasControls)."""
        self._virtual_source = fn

    # ---- ~60 Hz tick: bridge UDP both ways ----
    def _tick(self) -> None:
        # rx physical HOTAS
        try:
            while True:
                raw, _ = self._sock_hotas.recvfrom(8192)
                try:
                    self._phys_controls = HotasControls.from_json(raw)
                    self._last_phys_rx = time.monotonic()
                except MessageError:
                    continue
        except BlockingIOError:
            pass
        # precedence [a: 01.08b]: fresh physical input wins; otherwise the
        # virtual HOTAS (if registered) is the active source
        self.physical_active = (time.monotonic() - self._last_phys_rx
                                < self.PHYSICAL_TIMEOUT_S)
        if self.physical_active or self._virtual_source is None:
            base = self._phys_controls
        else:
            base = self._virtual_source()
        self.last_controls = base
        # AP must keep steering even without fresh stick input [b]
        cmd = HotasControls(**vars(base))
        cmd = self._autopilot.apply(cmd, self.last_telemetry)
        try:
            self._hotas_q.put_nowait(cmd)
        except queue.Full:
            pass

        # rx radar beacons (tombstone + staleness protocol [a: messages.py])
        try:
            while True:
                raw, _ = self._sock_radar.recvfrom(8192)
                try:
                    b = RadarBeacon.from_json(raw)
                except MessageError:
                    continue
                if b.active:
                    self.beacons[b.beacon_id] = (b, time.monotonic())
                else:
                    self.beacons.pop(b.beacon_id, None)
                self.beacon_updated.emit(b)          # i_BeaconDataOut
        except BlockingIOError:
            pass
        now = time.monotonic()
        for bid in [k for k, (_, t) in self.beacons.items()
                    if now - t > BEACON_STALE_TIMEOUT_S]:
            del self.beacons[bid]                    # last-resort GC

        # drain physics telemetry (latest value), retransmit ownship state
        tm = None
        try:
            while True:
                tm = self._telem_q.get_nowait()      # i_OwnTelemetryIn
        except queue.Empty:
            pass
        if tm is not None:
            self.last_telemetry = tm
            self._tx_seq += 1
            tm.seq = self._tx_seq
            self._sock_tx.sendto(                    # e_ownshipStateOut [a]
                tm.to_json(),
                (ports.LOCALHOST, ports.UDP_PCS_OWNSHIP_STATE))
            self.ownship_updated.emit(tm)            # i_OwnStateOut

    # ---- terrainElevationManager: e_groundElevMeterIn HTTP 8080 [a] ----
    def _elevation_worker(self) -> None:
        import requests
        url = (f"http://{ports.LOCALHOST}:{ports.HTTP_GROUND_ELEV}"
               f"/json/position/ground-elev-m")     # [c] FG httpd prop path
        while not self._elev_stop.is_set():
            try:
                r = requests.get(url, timeout=0.5)
                v = r.json().get("value")
                if v is not None:
                    self.ground_elev_m = float(v)   # FG returns a string
                    if self.last_telemetry is not None:
                        self.agl_m = (self.last_telemetry.alt_m
                                      - self.ground_elev_m)
            except Exception:
                self.agl_m = None                   # FG not running: no AGL
                self.ground_elev_m = None
            self._elev_stop.wait(0.5)               # 2 Hz [c]

    def shutdown(self) -> None:
        self._timer.stop()
        self._elev_stop.set()
        for s in (self._sock_hotas, self._sock_radar, self._sock_tx):
            s.close()


# ===========================================================================
# UserInterfaceManager  [a: part uiManager] -- the Qt main window
# ===========================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PCS - Pilot Control Station (FlightSim)")

        # Internal signal plumbing (InternalSignalPort realizations [a])
        self.hotas_q: queue.Queue = queue.Queue(maxsize=4)
        self.telem_q: queue.Queue = queue.Queue(maxsize=1)

        self.autopilot = Autopilot()
        self.logger = TelemetryLogger()
        self.warning_state = ""                     # [c] simple warning line
        self._fg_process: subprocess.Popen | None = None
        self._sim_running = True
        self._last_map_snapshot = None              # i_2dTileIn store [c]

        # Selected airport: SHARED state driving both ground start and
        # OTW launch [a-pending: L2SYS-REQ-01.04/5, ssfSelectAirport]
        self.selected_icao = DEFAULT_ICAO

        # Thread 2: physics [a: fsim_TA_PCS.sysml part threadPhysics]
        # Default start: ON GROUND at the selected airport [a-pending]
        self.physics = ThreadPhysics(self.hotas_q, self.telem_q,
                                     ic=self._airport_ic())
        self.physics.start()

        # ioManager [a]
        self.io = IOManager(self.hotas_q, self.telem_q, self.autopilot)

        # Thread 3: map window [a: fsim_TA_PCS.sysml part threadMapWindow;
        # separate top-level window spawned by the PCS main thread]
        self.map_window = MapWindow(tile_cache_path="tile_cache")
        # bindings [a: fsim_TA_PCS.sysml]:
        self.io.ownship_updated.connect(self.map_window.on_ownship)   # i_OwnStateOut -> i_OwnStateIn
        self.io.beacon_updated.connect(self.map_window.on_beacon)     # i_BeaconDataOut -> i_BeaconDataIn
        self.map_window.set_tile_cache_path("tile_cache")             # i_TileCachePathOut -> i_TileCachePathIn [c: fixed path]
        self.map_window.snapshot_ready.connect(self._on_2d_tile)      # i_2dTacticalMapOut -> i_2dTileIn
        self.map_window.show()

        self._build_ui()
        self._build_ssf_menu()
        self._wire_ssf_buttons()

        # updateCockpitDisplays [a] -- 10 Hz [c]
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._update_displays)
        self._ui_timer.start(100)

    # ------------------------------------------------------------------
    # Cockpit layout
    # ------------------------------------------------------------------
    DARK_QSS = """
        QMainWindow, QWidget { background: #101418; color: #d8dde2;
            font-family: Consolas, monospace; }
        QGroupBox { border: 1px solid #2a323a; margin-top: 8px;
            font-weight: bold; color: #9fb2c0; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; }
        QLabel { color: #d8dde2; }
        QPushButton { background: #1b232b; border: 1px solid #33404c;
            padding: 5px 10px; color: #d8dde2; }
        QPushButton:hover { background: #24303c; }
        QPushButton:disabled { color: #566068; background: #151a1f; }
        QPushButton:checked { background: #2e4d2e; border-color: #4a7a4a; }
        QDoubleSpinBox, QComboBox, QPlainTextEdit { background: #0c1014;
            border: 1px solid #33404c; color: #cfe3cf; }
        QMenuBar { background: #101418; color: #d8dde2; }
        QMenuBar::item:selected, QMenu::item:selected { background: #24303c; }
        QMenu { background: #161c22; color: #d8dde2; }
        QStatusBar { color: #9fb2c0; }
    """

    def _build_ui(self) -> None:
        """[b: UI-1, layout per customer reference screenshots 2026-07-11]
        Panels: attention getter (top), ownship readouts, System Controls,
        Navigation Controls, Mission Control, Sim Control (SSF buttons) +
        message log. Instruments (gauges) follow in UI-2; virtual HOTAS
        and PULL UP logic in UI-4 (requirements pending)."""
        self.setStyleSheet(self.DARK_QSS)
        central = QWidget()
        root = QVBoxLayout(central)

        # --- Attention getter strip [c: placeholder; PULL UP logic = UI-4]
        # [a: L2SYS-REQ-01.08c "Attention Getters" display slot;
        #  02.4 PULL UP logic follows in UI-4]
        self.lbl_attention = QLabel("")
        self.lbl_attention.setAlignment(Qt.AlignCenter)
        self.lbl_attention.setFixedHeight(34)
        self.lbl_attention.setStyleSheet(
            "background:#0c1014;border:1px solid #33404c;"
            "font-size:16px;font-weight:bold;color:#ff5050;")
        root.addWidget(self.lbl_attention)

        # --- Ownship readouts (compact; gauges come in UI-2) ---
        pfd = QGroupBox("Ownship State")
        grid = QGridLayout(pfd)
        self._val: dict[str, QLabel] = {}
        for i, (key, label) in enumerate([
                ("alt", "Alt MSL [m]"), ("agl", "AGL [m]"),
                ("spd", "GS [m/s]"), ("vs", "V/S [m/s]"),
                ("hdg", "HDG [deg]"), ("pitch", "Pitch"),
                ("roll", "Roll"), ("pos", "Lat / Lon"),
                ("simt", "Sim t [s]"),
                ("roll_cmd", "Ail cmd"), ("pitch_cmd", "Elev cmd"),
                ("yaw_cmd", "Rud cmd"), ("thr_cmd", "Thr cmd")]):
            grid.addWidget(QLabel(label + ":"), i // 3, (i % 3) * 2)
            v = QLabel("--")
            v.setStyleSheet("font-weight:bold;color:#7fd67f;")
            # [b: bug fix 2026-07-12] fixed width + right alignment so
            # values don't jitter the grid as text length changes
            v.setMinimumWidth(150 if key == "pos" else 90)
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(v, i // 3, (i % 3) * 2 + 1)
            self._val[key] = v
        root.addWidget(pfd)

        mid = QHBoxLayout()

        # --- Virtual HOTAS [a: L2SYS-REQ-01.08b] ---
        self.virtual_hotas = VirtualHotas()
        self.io.set_virtual_source(self.virtual_hotas.read)
        mid.addWidget(self.virtual_hotas, 2)

        # --- System Controls [b: ref GUI] ---
        # [a: L2SYS-REQ-01.06; buttons: 01.6b engine, 01.6c park
        #  brake, 01.6d air brake, 01.6e landing gear]
        sysg = QGroupBox("System Controls")
        sv = QGridLayout(sysg)
        self.btn_prk = QPushButton("PRK BRK: ON")
        self.cb_engine = QPushButton("ENGINE: OFF")   # checkable button:
        self.cb_engine.setCheckable(True)             # same API as the old
        self.btn_airbrk = QPushButton("AIR BRAKE: OFF")  # QCheckBox [b]
        self.btn_gear = QPushButton("GEAR: DOWN")
        self.btn_prk.clicked.connect(self._toggle_parking_brake)
        self.cb_engine.toggled.connect(self._on_engine_toggle)
        self.btn_airbrk.clicked.connect(self._toggle_airbrake)
        self.btn_gear.clicked.connect(self._toggle_gear)
        sv.addWidget(self.btn_prk, 0, 0); sv.addWidget(self.cb_engine, 0, 1)
        sv.addWidget(self.btn_airbrk, 1, 0); sv.addWidget(self.btn_gear, 1, 1)
        self.lbl_engine = QLabel("OFF")   # kept for API compat [b]
        self.lbl_engine.setVisible(False)
        mid.addWidget(sysg, 2)

        # --- Navigation Controls (AP/AH/AT) ---
        # [a: L2SYS-REQ-01.11 autopilot targets + AP/AH/AT]
        nav = QGroupBox("Navigation Controls")
        nv = QGridLayout(nav)
        self.cb_ap = QPushButton("AP"); self.cb_ap.setCheckable(True)
        self.cb_ah = QPushButton("AH"); self.cb_ah.setCheckable(True)
        self.cb_at = QPushButton("AT"); self.cb_at.setCheckable(True)
        self.sp_hdg = QDoubleSpinBox(); self.sp_hdg.setRange(0, 360)
        self.sp_hdg.setValue(self.autopilot.target_heading_deg)
        self.sp_alt = QDoubleSpinBox(); self.sp_alt.setRange(0, 20000)
        self.sp_alt.setValue(self.autopilot.target_alt_m)
        self.sp_spd = QDoubleSpinBox(); self.sp_spd.setRange(0, 400)
        self.sp_spd.setValue(self.autopilot.target_speed_ms)
        # [b: bug fix 2026-07-12, sync-on-engage] engaging a mode captures
        # the CURRENT flight state as its target -- otherwise stale
        # default targets command an immediate (and surprising) maneuver,
        # e.g. a sustained 25-30 deg bank toward an old heading target
        def _engage(mode: str, spin, current):
            def handler(v):
                if v:
                    tm = self.io.last_telemetry
                    if tm is not None:
                        spin.setValue(current(tm))
                setattr(self.autopilot, mode, v)
            return handler
        self.cb_ap.toggled.connect(_engage(
            "ap_engaged", self.sp_hdg, lambda t: t.heading_deg))
        self.cb_ah.toggled.connect(_engage(
            "ah_engaged", self.sp_alt, lambda t: t.alt_m))
        self.cb_at.toggled.connect(_engage(
            "at_engaged", self.sp_spd, lambda t: t.groundspeed_ms))
        self.sp_hdg.valueChanged.connect(lambda v: setattr(self.autopilot, "target_heading_deg", v))
        self.sp_alt.valueChanged.connect(lambda v: setattr(self.autopilot, "target_alt_m", v))
        self.sp_spd.valueChanged.connect(lambda v: setattr(self.autopilot, "target_speed_ms", v))
        nv.addWidget(QLabel("Alt [m]:"), 0, 0); nv.addWidget(self.sp_alt, 0, 1)
        nv.addWidget(self.cb_ah, 0, 2)
        nv.addWidget(QLabel("Spd [m/s]:"), 1, 0); nv.addWidget(self.sp_spd, 1, 1)
        nv.addWidget(self.cb_at, 1, 2)
        nv.addWidget(QLabel("Hdg [deg]:"), 2, 0); nv.addWidget(self.sp_hdg, 2, 1)
        nv.addWidget(self.cb_ap, 2, 2)
        mid.addWidget(nav, 3)

        # --- Mission Control: airport [a: ssfSelectAirport, freeze-gated]
        mis = QGroupBox("Mission Control")
        mv = QVBoxLayout(mis)
        mv.addWidget(QLabel("Location:"))
        self.cmb_airport = QComboBox()
        for a in AIRPORTS.values():
            self.cmb_airport.addItem(f"{a.name} ({a.icao})", a.icao)
        self.cmb_airport.setCurrentIndex(
            list(AIRPORTS).index(self.selected_icao))
        self.cmb_airport.currentIndexChanged.connect(self._on_airport_combo)
        mv.addWidget(self.cmb_airport)
        mv.addStretch()
        self.lbl_beacons = QLabel("Beacons: 0")
        mv.addWidget(self.lbl_beacons)
        mid.addWidget(mis, 2)

        root.addLayout(mid)

        # --- Sim Control: SSF button rows [b: ref GUI bottom panel] ---
        # [a: L2SYS-REQ-01.10 Sim Control Panel; 01.10b SSF set]
        sim = QGroupBox("Sim Control")
        sgrid = QGridLayout(sim)
        self._ssf_buttons: dict[str, QPushButton] = {}
        def sbtn(row, col, text, key):
            b = QPushButton(text)
            sgrid.addWidget(b, row, col)
            self._ssf_buttons[key] = b
            return b
        sbtn(0, 0, "RUN SIM", "start")
        sbtn(0, 1, "PAUSE SIM", "stop")
        sbtn(0, 2, "RESET SIM", "reset")
        sbtn(0, 3, "LAUNCH 3D OTW", "otw")
        sbtn(0, 4, "SET POSITION", "setpos")
        sbtn(1, 0, "Save Snapshot", "save")
        sbtn(1, 1, "Load Snapshot", "load")
        f = sbtn(1, 2, "FUEL FREEZE", "fuel"); f.setCheckable(True)
        t = sbtn(1, 3, "Telemetry Capture", "log"); t.setCheckable(True)
        root.addWidget(sim)

        # --- Message log [b: ref GUI status box] ---
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedHeight(64)
        root.addWidget(self.txt_log)

        self.setStatusBar(QStatusBar())
        self.setCentralWidget(central)

    def _log(self, msg: str) -> None:
        self.txt_log.appendPlainText(msg)
        self.statusBar().showMessage(msg, 5000)

    # --- System Controls handlers [b: mirror HOTAS toggles] ---
    def _toggle_parking_brake(self) -> None:
        self.physics.set_parking_brake(not self.physics.parking_brake)

    def _toggle_airbrake(self) -> None:
        self.physics.set_airbrake(not self.physics.airbrake_out)

    def _toggle_gear(self) -> None:
        self.physics.set_gear(not self.physics.gear_down)

    def _on_airport_combo(self, idx: int) -> None:
        icao = self.cmb_airport.itemData(idx)
        if icao and icao != self.selected_icao:
            self.selected_icao = icao
            self._clear_pilot_state()
            self.physics.ssf_reset(self._airport_ic())
            self._log(f"Airport set: {icao} -- aircraft repositioned "
                      f"(frozen). RUN SIM to fly.")

    # ------------------------------------------------------------------
    # SSF menu -- manageSSF [a], freeze-caveat enforcement [a]
    # ------------------------------------------------------------------
    def _build_ssf_menu(self) -> None:
        m = self.menuBar().addMenu("&SSF")
        self._freeze_only_actions: list[QAction] = []

        def add(text, slot, freeze_only=False):
            a = QAction(text, self)
            a.triggered.connect(slot)
            m.addAction(a)
            if freeze_only:
                self._freeze_only_actions.append(a)
            return a

        self.act_start = add("Start Simulation", self._ssf_start)
        self.act_stop = add("Stop Simulation (freeze)", self._ssf_stop)
        m.addSeparator()
        add("Reset Simulation", self._ssf_reset, freeze_only=True)
        add("Select Airport...", self._ssf_select_airport, freeze_only=True)
        add("Set Own Position...", self._ssf_set_own_position, freeze_only=True)
        add("Save Snapshot...", self._ssf_save_snapshot, freeze_only=True)
        add("Load Snapshot...", self._ssf_load_snapshot, freeze_only=True)
        m.addSeparator()
        add("Launch OTW View (FlightGear)...", self._ssf_launch_otw)
        self.act_fuel = QAction("Freeze Fuel Consumption", self, checkable=True)
        self.act_fuel.toggled.connect(
            lambda v: self.physics.ssf_freeze_fuel(v))
        m.addAction(self.act_fuel)
        self.act_log = QAction("Log Telemetry to CSV...", self, checkable=True)
        self.act_log.toggled.connect(self._ssf_log_telemetry)
        m.addAction(self.act_log)
        self._apply_freeze_gate()

    def _wire_ssf_buttons(self) -> None:
        """Buttons trigger the same QActions as the SSF menu, so the
        tested slots and the freeze gate stay single-source [b]."""
        B, A = self._ssf_buttons, self._freeze_only_actions
        B["start"].clicked.connect(self.act_start.trigger)
        B["stop"].clicked.connect(self.act_stop.trigger)
        by_text = {a.text(): a for a in A}
        B["reset"].clicked.connect(by_text["Reset Simulation"].trigger)
        B["setpos"].clicked.connect(by_text["Set Own Position..."].trigger)
        B["save"].clicked.connect(by_text["Save Snapshot..."].trigger)
        B["load"].clicked.connect(by_text["Load Snapshot..."].trigger)
        self._btn_action_pairs = [
            (B["start"], self.act_start), (B["stop"], self.act_stop),
            (B["reset"], by_text["Reset Simulation"]),
            (B["setpos"], by_text["Set Own Position..."]),
            (B["save"], by_text["Save Snapshot..."]),
            (B["load"], by_text["Load Snapshot..."]),
        ]
        B["otw"].clicked.connect(self._ssf_launch_otw)
        B["fuel"].toggled.connect(self.act_fuel.setChecked)
        self.act_fuel.toggled.connect(B["fuel"].setChecked)
        B["log"].toggled.connect(self.act_log.setChecked)
        self.act_log.toggled.connect(B["log"].setChecked)

    def _apply_freeze_gate(self) -> None:
        """[a] caveat: reset/setpos/save/load only during flight-freeze."""
        for a in self._freeze_only_actions:
            a.setEnabled(not self._sim_running)
        self.act_start.setEnabled(not self._sim_running)
        self.act_stop.setEnabled(self._sim_running)

    def _on_engine_toggle(self, on: bool) -> None:
        """[a: L2SYS-REQ-01.06b] ENGINE toggle-button command.
        [c] realism gate: start refused unless throttle is at idle
        (<10%), like the real aircraft -- retard throttle first."""
        if on and self.io.last_controls.throttle > 0.10:
            self.cb_engine.blockSignals(True)
            self.cb_engine.setChecked(False)
            self.cb_engine.blockSignals(False)
            self.statusBar().showMessage(
                "Engine start refused: retard throttle to idle first.", 5000)
            return
        self.physics.set_engine(on)

    def _airport_ic(self) -> InitialConditions:
        """Ground-start IC at the selected airport [a-pending: 01.4]."""
        a = AIRPORTS[self.selected_icao]
        return InitialConditions(
            lat_deg=a.lat_deg, lon_deg=a.lon_deg,
            heading_deg=a.rwy_heading_deg,
            on_ground=True, terrain_elev_m=a.elev_m)

    # ---- SSF implementations ----
    def _ssf_select_airport(self) -> None:  # ssfSelectAirport [a-pending]
        items = [f"{a.icao}  {a.name}  (rwy {a.rwy})"
                 for a in AIRPORTS.values()]
        cur = list(AIRPORTS).index(self.selected_icao)
        pick, ok = QInputDialog.getItem(self, "Select Airport",
            "Airport (sets ground start + OTW location):", items, cur, False)
        if not ok:
            return
        self.selected_icao = pick.split()[0]
        # selecting an airport repositions the frozen a/c there [b]
        self.physics.ssf_reset(self._airport_ic())
        self.statusBar().showMessage(
            f"Airport set: {self.selected_icao} -- aircraft repositioned "
            f"(frozen). Start Simulation to fly.", 8000)

    def _ssf_start(self) -> None:                    # ssfStartSimulation [a]
        self.physics.ssf_start()
        self._sim_running = True
        self._apply_freeze_gate()

    def _ssf_stop(self) -> None:                     # ssfStopSimulation [a]
        self.physics.ssf_stop()
        self._sim_running = False
        self._apply_freeze_gate()

    def _clear_pilot_state(self) -> None:
        """[b: bug fix 2026-07-12] no residual state across a reset:
        disengage AP/AH/AT and fuel freeze (physics side is cleaned by
        the ground-start IC branch: engine OFF, PRK BRK ON, gear down)."""
        for cb in (self.cb_ap, self.cb_ah, self.cb_at):
            cb.setChecked(False)
        self.act_fuel.setChecked(False)

    def _ssf_reset(self) -> None:                    # ssfResetSimulation [a]
        # [a: UIM doc "aircraft at base runway"] -> ground at selected apt
        self._clear_pilot_state()
        self.physics.ssf_reset(self._airport_ic())

    def _ssf_set_own_position(self) -> None:         # ssfSetOwnPosition [a]
        tm = self.io.last_telemetry
        lat, ok = QInputDialog.getDouble(self, "Set Own Position",
            "Latitude [deg]:", tm.lat_deg if tm else 45.45, -90, 90, 5)
        if not ok:
            return
        lon, ok = QInputDialog.getDouble(self, "Set Own Position",
            "Longitude [deg]:", tm.lon_deg if tm else 9.28, -180, 180, 5)
        if not ok:
            return
        alt, ok = QInputDialog.getDouble(self, "Set Own Position",
            "Altitude MSL [m]:", tm.alt_m if tm else 1500, 0, 30000, 1)
        if not ok:
            return
        self.physics.ssf_set_own_position(lat, lon, alt)
        self._log(f"Own position set: {lat:.4f}, {lon:.4f}, {alt:.0f} m")

    def _snapshot_state(self) -> dict:
        """[c] snapshot scope: physics + AP targets + beacons + fuel flag.
        Note: dict(...) copies are essential -- vars() alone returns live
        references that would mutate with the running sim."""
        tm = self.io.last_telemetry
        return {
            "saved_utc": datetime.now(timezone.utc).isoformat(),
            "ownship": dict(vars(tm)) if tm else None,
            "autopilot": dict(vars(self.autopilot)),
            "fuel_frozen": self.act_fuel.isChecked(),
            "beacons": {bid: dict(vars(b))
                        for bid, (b, _) in self.io.beacons.items()},
        }

    def _ssf_save_snapshot(self) -> None:            # ssfSaveSnapshot [a]
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Snapshot", "snapshot.json", "Snapshot (*.json)")
        if not path:
            return
        with open(path, "w") as f:
            json.dump(self._snapshot_state(), f, indent=2)
        self._log(f"Snapshot saved: {path}")

    def _ssf_load_snapshot(self) -> None:            # ssfLoadSnapshot [a]
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Snapshot", "", "Snapshot (*.json)")
        if not path:
            return
        with open(path) as f:
            snap = json.load(f)
        self.apply_snapshot(snap)
        self._log(f"Snapshot loaded: {path}")

    def apply_snapshot(self, snap: dict) -> None:
        own = snap.get("ownship")
        if own:
            self.physics.ssf_set_own_position(
                own["lat_deg"], own["lon_deg"], own["alt_m"],
                own.get("heading_deg", 0.0),
                max(60.0, own.get("groundspeed_ms", 60.0) / 0.514444))
        for k, v in snap.get("autopilot", {}).items():
            setattr(self.autopilot, k, v)
        self.cb_ap.setChecked(self.autopilot.ap_engaged)
        self.cb_ah.setChecked(self.autopilot.ah_engaged)
        self.cb_at.setChecked(self.autopilot.at_engaged)
        self.sp_hdg.setValue(self.autopilot.target_heading_deg)
        self.sp_alt.setValue(self.autopilot.target_alt_m)
        self.sp_spd.setValue(self.autopilot.target_speed_ms)
        self.act_fuel.setChecked(bool(snap.get("fuel_frozen")))
        # [c] beacons in a snapshot are informational only: the live
        # picture is owned by the SEM (single source of truth); the PCS
        # does not inject beacons from a file.

    # [a: legacy launch_flightgear(), field-proven 2026-07-11]
    FG_AIRCRAFT_DIR = r"C:\Users\[redacted]\Documents\FlightGearAircraft"

    def _ssf_launch_otw(self) -> None:               # ssfLaunchOtwView [a]
        """Launch FlightGear per the legacy, field-proven command line:
        YF-16 from the custom aircraft dir, chosen airport, noon light,
        native-fdm at 60 Hz (matches physics decimation), httpd 8080 for
        the ground-elevation service, and FG's OWN joystick input
        DISABLED via the two js props -- essential, or FG fights the
        external FDM stream [a: legacy]."""
        icao = self.selected_icao   # [a-pending: 01.5] consistency with
        #                             the ownship ground-start location
        cmd = ["fgfs",
               "--fdm=null",
               f"--native-fdm=socket,in,60,,{ports.UDP_NATIVE_FDM},udp",
               f"--fg-aircraft={self.FG_AIRCRAFT_DIR}",
               "--aircraft=YF-16",
               f"--airport={icao}",
               "--timeofday=noon",
               f"--httpd={ports.HTTP_GROUND_ELEV}",
               "--prop:/input/joysticks/js[0]=0",
               "--prop:/input/joysticks/js[1]=0"]
        try:
            self._fg_process = subprocess.Popen(cmd)
            self._log(f"LAUNCHING FLIGHTGEAR 3D ENGINE AT {icao}...")
            self._log("Waiting for visual database...")
        except FileNotFoundError:
            QMessageBox.warning(self, "FlightGear not found",
                "fgfs executable not on PATH. Install FlightGear or launch "
                "manually:\n" + " ".join(cmd))
        except Exception as e:                       # [a: legacy handler]
            QMessageBox.warning(self, "OTW launch error", str(e))

    def _ssf_log_telemetry(self, on: bool) -> None:  # ssfLogTelemetry [a]
        if on:
            path, _ = QFileDialog.getSaveFileName(
                self, "Telemetry CSV", "telemetry.csv", "CSV (*.csv)")
            if not path:
                self.act_log.setChecked(False)
                return
            self.logger.start(path)
        else:
            self.logger.stop()

    # ------------------------------------------------------------------
    # updateCockpitDisplays [a] -- 10 Hz UI refresh + CSV logging
    # ------------------------------------------------------------------
    def _update_displays(self) -> None:
        tm = self.io.last_telemetry
        c = self.io.last_controls
        ap = self.autopilot
        if tm:
            self._val["alt"].setText(f"{tm.alt_m:8.1f}")
            self._val["agl"].setText(
                f"{self.io.agl_m:8.1f}" if self.io.agl_m is not None else "n/a")
            self._val["spd"].setText(f"{tm.groundspeed_ms:6.1f}")
            self._val["vs"].setText(f"{tm.vertical_speed_ms:+6.2f}")
            self._val["hdg"].setText(f"{tm.heading_deg:6.1f}")
            self._val["pitch"].setText(f"{tm.pitch_deg:+6.1f}")
            self._val["roll"].setText(f"{tm.roll_deg:+6.1f}")
            self._val["pos"].setText(f"{tm.lat_deg:9.4f} / {tm.lon_deg:9.4f}")
            self._val["simt"].setText(f"{tm.sim_time_s:8.1f}")
        eff = self.autopilot.apply(HotasControls(**vars(c)), tm)
        self._val["roll_cmd"].setText(f"{eff.roll:+.2f}")
        self._val["pitch_cmd"].setText(f"{eff.pitch:+.2f}")
        self._val["yaw_cmd"].setText(f"{eff.yaw:+.2f}")
        self._val["thr_cmd"].setText(f"{eff.throttle:.2f}")
        eng_on = self.physics.engine_on
        self.lbl_engine.setText("RUNNING" if eng_on else "OFF")
        self.warning_state = "" if eng_on else "ENGINE OFF"
        self.cb_engine.setText("ENGINE: ON" if eng_on else "ENGINE: OFF")
        self.cb_engine.setStyleSheet(
            "background:%s;" % ("#2e4d2e" if eng_on else "#5a1f1f"))
        prk = self.physics.parking_brake
        self.btn_prk.setText("PRK BRK: ON" if prk else "PRK BRK: OFF")
        self.btn_prk.setStyleSheet(
            "background:%s;" % ("#7a5215" if prk else "#1b232b"))
        ab = self.physics.airbrake_out
        self.btn_airbrk.setText("AIR BRAKE: OUT" if ab else "AIR BRAKE: OFF")
        self.btn_airbrk.setStyleSheet(
            "background:%s;" % ("#7a5215" if ab else "#1b232b"))
        gd = self.physics.gear_down
        self.btn_gear.setText("GEAR: DOWN" if gd else "GEAR: UP")
        self.btn_gear.setStyleSheet(
            "background:%s;" % ("#2e4d2e" if gd else "#5a1f1f"))
        for btn, act in getattr(self, "_btn_action_pairs", []):
            btn.setEnabled(act.isEnabled())
        self.cmb_airport.setEnabled(not self._sim_running)
        self.virtual_hotas.set_physical_active(self.io.physical_active)
        if self.io.physical_active:
            # [b: customer request 2026-07-13] widget follows the stick
            self.virtual_hotas.mirror(self.io.last_controls)
        # [b: bug fix 2026-07-12] feed FG ground elevation into JSBSim so
        # FDM terrain matches the OTW scenery (no more taxiing underground)
        if self.io.ground_elev_m is not None:
            self.physics.set_terrain_elevation(self.io.ground_elev_m)
        if self.cb_engine.isChecked() != eng_on:      # sync after reset [b]
            self.cb_engine.blockSignals(True)
            self.cb_engine.setChecked(eng_on)
            self.cb_engine.blockSignals(False)
        self.lbl_beacons.setText(
            f"Beacons: {len(self.io.beacons)} "
            f"({sum(1 for b, _ in self.io.beacons.values() if b.detected)} detecting)")

        if self.logger.active and tm:
            self.logger.log({
                "wall_time_iso": datetime.now(timezone.utc).isoformat(),
                "sim_time_s": tm.sim_time_s, "model": self.physics._aircraft,
                "lat_deg": tm.lat_deg, "lon_deg": tm.lon_deg,
                "alt_m": tm.alt_m,
                "agl_m": self.io.agl_m if self.io.agl_m is not None else "",
                "v_speed_mps": tm.vertical_speed_ms,
                "speed_mps": tm.groundspeed_ms,
                "heading_deg": tm.heading_deg,
                "target_heading_deg": ap.target_heading_deg,
                "pitch_deg": tm.pitch_deg,
                "pitch_rate_dps": "",                 # [c] not in OwnshipState
                "roll_deg": tm.roll_deg,
                "target_alt_m": ap.target_alt_m,
                "target_speed_mps": ap.target_speed_ms,
                "aileron_cmd": eff.roll, "elevator_cmd": eff.pitch,
                "rudder_cmd": eff.yaw, "throttle_cmd": eff.throttle,
                "gear_cmd": self.physics._gear_down,  # btn2 toggle [a]
                "trim_pitch": "", "trim_roll": "", "trim_yaw": "",  # [c] TBD
                "ap_engaged": ap.ap_engaged, "ah_engaged": ap.ah_engaged,
                "at_engaged": ap.at_engaged,
                "warning_state": self.warning_state,
            })

    def _on_2d_tile(self, pixmap) -> None:
        """i_2dTileIn [a]: 1 Hz map snapshot from the Map Window. Stored
        for cockpit-display embedding; visual integration deferred to the
        UI-improvement backlog [c]."""
        self._last_map_snapshot = pixmap

    def closeEvent(self, event) -> None:
        self.map_window.close()
        self.logger.stop()
        self.io.shutdown()
        self.physics.shutdown()
        self.physics.join(timeout=2)
        if self._fg_process:
            self._fg_process.terminate()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(760, 420)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()