"""
flightsim/pcs/main_window.py
============================
Pilot Control Station: the UI and I/O assembly thread.

TRACEABILITY
------------
[a] Implements part def ThreadFlightSim (fsim_TA_PCS_ThreadFlightSim.sysml),
    whose doc comment names this file: "UI and I/O Loop Assembly
    (main_window.py)", with its two submodules:
      - UserInterfaceManager (fsim_TA_PCS_ThreadFlightSim_UIM.sysml)
        -> class PCSMainWindow (controlUserInputs, updateCockpitDisplays,
           manageSSF and all ssf* actions)
      - BackgroundIOManager (fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml)
        -> class BackgroundIOManager (bridgeUdpData,
           telemetryCaptureManager, terrainElevationManager)
[a] Boundary ports per fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml:
    e_hotasControlIn 5005, e_ownshipStateOut 5006, e_radarDetectionsIn
    6006, e_groundElevMeterIn 8080 [a: D4].
[a] Runtime: PythonRuntime version "3.14" (fsim_TA_PCS.sysml) -> .venv_fsim
    [a: D5].

REQUIREMENTS IMPLEMENTED HERE
-----------------------------
[a] L2SYS-REQ-01.04b - airport selection only during flight-freeze.
[a] L2SYS-REQ-01.06  - System Controls Panel.
[a] L2SYS-REQ-01.06b-e - ENGINE / PARK BRAKE / AIR BRAKE / LDG GEAR
                         toggle-buttons.
[a] L2SYS-REQ-01.08  - the virtual cockpit console (PCS) itself.
[a] L2SYS-REQ-01.08b - virtual flight controls with physical precedence
                       (panel implemented in virtual_hotas.py).
[a] L2SYS-REQ-01.08c - virtual gauges and displays (Airspeed, Attitude,
                       Fuel, Heading, RPM, HUD, Attention Getters,
                       Message Window).
[a] L2SYS-REQ-01.10  - Sim Control Panel.
[a] L2SYS-REQ-01.10b - the nine ARINC-610 SSF.
[a] L2SYS-REQ-01.11  - Navigation Controls Panel (Alt/Spd/Hdg + AP/AH/AT).
[a] L2SYS-REQ-02.03  - receive the threat detections from the SEM.
[a] L2SYS-REQ-02.04  - ground-proximity attention getter (see amendment
                       A-1 of docs/VERIFICATION_PLAN.md).

DECISIONS IMPLEMENTED HERE
--------------------------
[a: D6]  the FlightGear launch command line, verbatim.
[a: D9]  2 Hz terrain-elevation poll wired into the physics thread.
[a: D10] physical-precedence window and virtual fallback.
[a: D11] autopilot control laws.
[a: D14] panel layout, SSF button grid sharing one freeze gate with the
         menu, telemetry CSV header (28 fields), dark QSS.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import math
import os
import socket
import subprocess
import time
from typing import Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from flightsim.common import config as fg_config
from flightsim.common import ports
from flightsim.common.messages import (
    BeaconStore, HotasControls, MessageError, OwnshipState, RadarBeacon,
)
from flightsim.pcs.airports import AirportTable, DEFAULT_TABLE
from flightsim.pcs.map_window import ThreadMapWindow
from flightsim.pcs.physics import ThreadPhysics
from flightsim.pcs.virtual_hotas import PHYSICAL_PRECEDENCE_S, VirtualHotasWidget

# --------------------------------------------------------------------------
# D14 telemetry CSV header
# --------------------------------------------------------------------------
#: [a: D14] "Telemetry CSV header: the 28 fields verbatim from the
#: ssfLogTelemetry doc" (fsim_TA_PCS_ThreadFlightSim_UIM.sysml). The doc
#: comment separates two pairs with '.' instead of ','; read as field
#: separators that yields exactly the 28 fields D14 counts [b].
TELEMETRY_FIELDS = [
    "wall_time_iso", "sim_time_s", "model", "lat_deg", "lon_deg", "alt_m",
    "agl_m", "v_speed_mps", "speed_mps", "heading_deg", "target_heading_deg",
    "pitch_deg", "pitch_rate_dps", "roll_deg", "target_alt_m",
    "target_speed_mps", "aileron_cmd", "elevator_cmd", "rudder_cmd",
    "throttle_cmd", "gear_cmd", "trim_pitch", "trim_roll", "trim_yaw",
    "ap_engaged", "ah_engaged", "at_engaged", "warning_state",
]
assert len(TELEMETRY_FIELDS) == 28

# --------------------------------------------------------------------------
# D6 FlightGear launch
# --------------------------------------------------------------------------
#: [a: D6] "FlightGear visual YF-16 from a user aircraft dir".
FG_AIRCRAFT_MODEL = "YF-16"
#: [a: D6] the program name of the Decision Annex command line. Where that
#: program LIVES on a given machine is a deployment parameter, resolved from
#: the environment by flightsim.common.config [b].
FG_EXECUTABLE = fg_config.FG_PROGRAM_NAME


def build_flightgear_command(icao: str, aircraft_dir: str,
                             executable: Optional[str] = None,
                             fg_root: Optional[str] = None) -> List[str]:
    """[a: D6] the FG launch command line, exactly as the legacy
    launch_flightgear() function issues it:

        fgfs --fdm=null --native-fdm=socket,in,60,,5550,udp
             --fg-aircraft=<dir> --aircraft=YF-16 --airport=<ICAO>
             --timeofday=noon --httpd=8080
             --prop:/input/joysticks/js[0]=0
             --prop:/input/joysticks/js[1]=0

    [b] argv[0] is the RESOLVED path of that same program on this machine
    (flightsim.common.config, driven by the FSIM_FG_* environment
    variables). D6 fixes which program is run and with which flags; it does
    not state where the installer put it, and a bare program name only
    works when FlightGear's bin directory happens to be on PATH - which it
    is not after a stock Windows install. Every flag after argv[0] is
    unchanged and asserted token-for-token in the suite.

    [b] --fg-root is appended only when FSIM_FG_ROOT is set: it is a
    deployment escape hatch for non-standard data-directory layouts, absent
    by default so the nominal command line stays exactly the D6 one.
    """
    command = [
        executable or fg_config.flightgear_executable(),
        "--fdm=null",
        f"--native-fdm=socket,in,60,,{ports.UDP_FG_NATIVE_FDM},udp",
        f"--fg-aircraft={aircraft_dir}",
        f"--aircraft={FG_AIRCRAFT_MODEL}",
        f"--airport={icao}",
        "--timeofday=noon",
        f"--httpd={ports.HTTP_FG_HTTPD}",
        "--prop:/input/joysticks/js[0]=0",
        "--prop:/input/joysticks/js[1]=0",
    ]
    if fg_root:
        command.append(f"--fg-root={fg_root}")
    return command


# --------------------------------------------------------------------------
# 02.04 ground-proximity criterion
# --------------------------------------------------------------------------
#: [c] The requirement says "when height above ground and vertical speed
#: indicate imminent ground impact" without figures; a time-to-impact
#: threshold is the standard GPWS formulation. Flagged as a developer
#: choice.
GPWS_MAX_AGL_M = 300.0
GPWS_MIN_DESCENT_MPS = 3.0
GPWS_TIME_TO_IMPACT_S = 15.0
#: [c] Attention-getter flash period (01.10 "flash intermittently").
ATTENTION_FLASH_MS = 400


def ground_proximity_warning(state: OwnshipState) -> bool:
    """L2SYS-REQ-02.04: "when height above ground and vertical speed
    indicate imminent ground impact"."""
    if state.on_ground:
        return False
    if state.agl_m > GPWS_MAX_AGL_M:
        return False
    descent = -state.vspeed_mps
    if descent < GPWS_MIN_DESCENT_MPS:
        return False
    return (state.agl_m / descent) < GPWS_TIME_TO_IMPACT_S


# ==========================================================================
# Autopilot  [a: D11]
# ==========================================================================

def _clip(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else (hi if value > hi else value)


class Autopilot:
    """TA actions controlNavigationAP / controlNavigationAH /
    controlNavigationAT of UserInterfaceManager.

    [a] L2SYS-REQ-01.11: autopilot targets Altitude (m), Speed (m/s),
        Heading (deg) and the AP (heading hold), AH (altitude hold) and
        AT (autothrottle) modes.
    [a: D11] "engaging AP/AH/AT captures the current value as target
        (sync-on-engage)" plus the control laws implemented below.
    """

    def __init__(self):
        self.target_alt_m = 0.0
        self.target_speed_mps = 0.0
        self.target_heading_deg = 0.0
        self.ap_engaged = False       # heading hold
        self.ah_engaged = False       # altitude hold
        self.at_engaged = False       # autothrottle

    # -- sync-on-engage --------------------------------------------------
    def engage_ap(self, state: OwnshipState, engaged: bool = True) -> None:
        """[a: D11] engaging captures the current value as target."""
        if engaged and not self.ap_engaged:
            self.target_heading_deg = state.heading_deg
        self.ap_engaged = bool(engaged)

    def engage_ah(self, state: OwnshipState, engaged: bool = True) -> None:
        if engaged and not self.ah_engaged:
            self.target_alt_m = state.alt_m
        self.ah_engaged = bool(engaged)

    def engage_at(self, state: OwnshipState, engaged: bool = True) -> None:
        if engaged and not self.at_engaged:
            self.target_speed_mps = state.speed_mps
        self.at_engaged = bool(engaged)

    def disengage_all(self) -> None:
        """[a: D8] reset is a clean slate with the AP modes disengaged."""
        self.ap_engaged = self.ah_engaged = self.at_engaged = False

    # -- control laws ----------------------------------------------------
    @staticmethod
    def altitude_hold(state: OwnshipState, target_alt_m: float) -> float:
        """[a: D11] AH attitude cascade:
        vs_cmd  = clip(0.15 * alt_err, +-5 m/s)
        theta_cmd = clip(1.5 * (vs_cmd - vs), -8 deg, +12 deg)
        pitch   = clip(0.06 * (theta_cmd - pitch_deg), +-1)"""
        alt_err = float(target_alt_m) - state.alt_m
        vs_cmd = _clip(0.15 * alt_err, -5.0, 5.0)
        theta_cmd = _clip(1.5 * (vs_cmd - state.vspeed_mps), -8.0, 12.0)
        return _clip(0.06 * (theta_cmd - state.pitch_deg), -1.0, 1.0)

    @staticmethod
    def heading_hold(state: OwnshipState, target_heading_deg: float) -> float:
        """[a: D11] AP: bank_cmd = clip(0.8 * hdg_err, +-25 deg);
        roll = clip(0.03 * (bank_cmd - roll_deg))."""
        hdg_err = ((float(target_heading_deg) - state.heading_deg + 180.0)
                   % 360.0) - 180.0
        bank_cmd = _clip(0.8 * hdg_err, -25.0, 25.0)
        return _clip(0.03 * (bank_cmd - state.roll_deg), -1.0, 1.0)

    @staticmethod
    def autothrottle(state: OwnshipState, target_speed_mps: float) -> float:
        """[a: D11] AT: throttle = clip(0.5 + 0.04 * spd_err)."""
        spd_err = float(target_speed_mps) - state.speed_mps
        return _clip(0.5 + 0.04 * spd_err, 0.0, 1.0)

    def apply(self, state: OwnshipState,
              controls: HotasControls) -> HotasControls:
        """Overlay the engaged modes on the pilot's manual commands.
        [a] L2SYS-REQ-01.11."""
        out = HotasControls(roll=controls.roll, pitch=controls.pitch,
                            yaw=controls.yaw, throttle=controls.throttle,
                            switches=dict(controls.switches),
                            seq=controls.seq)
        if self.ah_engaged:
            out.pitch = self.altitude_hold(state, self.target_alt_m)
        if self.ap_engaged:
            out.roll = self.heading_hold(state, self.target_heading_deg)
        if self.at_engaged:
            out.throttle = self.autothrottle(state, self.target_speed_mps)
        return out


# ==========================================================================
# BackgroundIOManager
# ==========================================================================

class BackgroundIOManager(QtCore.QObject):
    """part def BackgroundIOManager (fsim_TA_PCS_ThreadFlightSim_BkgIOM).

    "Handles background QTimers, network bridging, and telemetry capture."
    TA actions bridgeUdpData / telemetryCaptureManager /
    terrainElevationManager.
    """

    hotas_received = QtCore.pyqtSignal(object)
    beacon_received = QtCore.pyqtSignal(object)

    def __init__(self, host: str = ports.LOCALHOST, bind: bool = True,
                 parent=None):
        super().__init__(parent)
        self.host = host
        self.beacons = BeaconStore()                # [a: D2] receiver GC
        self.last_physical_time = 0.0               # [a: D10]
        self.last_physical: Optional[HotasControls] = None
        self.ownship_seq = 0
        self.published: List[OwnshipState] = []     # diagnostics
        self._hotas_sock: Optional[socket.socket] = None
        self._radar_sock: Optional[socket.socket] = None
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # -- telemetryCaptureManager --------------------------------------
        self.capture_path: Optional[str] = None
        self._capture_file = None
        self._capture_writer = None
        if bind:
            self.bind()

    # -- ports -----------------------------------------------------------
    def bind(self) -> None:
        self._hotas_sock = self._bind(ports.UDP_HOTAS_CONTROL)  # 5005
        self._radar_sock = self._bind(ports.UDP_ORCH_TO_PCS)    # 6006

    def _bind(self, port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, int(port)))
        sock.setblocking(False)
        return sock

    # -- bridgeUdpData ---------------------------------------------------
    def poll(self, now: Optional[float] = None) -> None:
        """TA action bridgeUdpData: drain both inbound ports.

        [a] L2SYS-REQ-01.08b physical precedence timestamping,
            L2SYS-REQ-02.03 threat-detection reception.
        """
        now = time.monotonic() if now is None else now
        if self._hotas_sock is not None:
            while True:
                try:
                    data, _ = self._hotas_sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    break
                try:
                    msg = HotasControls.from_json(data)
                except MessageError:
                    continue                    # [a: D1] reject malformed
                self.last_physical = msg
                self.last_physical_time = now   # [a: D10]
                self.hotas_received.emit(msg)
        if self._radar_sock is not None:
            while True:
                try:
                    data, _ = self._radar_sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    break
                try:
                    beacon = RadarBeacon.from_json(data)
                except MessageError:
                    continue
                self.beacons.apply(beacon, now)   # [a: D2] incl. tombstones
                self.beacon_received.emit(beacon)
        self.beacons.collect(now)                 # [a: D2] stale GC

    def physical_active(self, now: Optional[float] = None) -> bool:
        """[a: D10] "physical precedence window 1.0 s (fresh 5005 datagram
        wins)"."""
        now = time.monotonic() if now is None else now
        if self.last_physical is None:
            return False
        return (now - self.last_physical_time) <= PHYSICAL_PRECEDENCE_S

    # -- e_ownshipStateOut ----------------------------------------------
    def publish_ownship(self, state: OwnshipState) -> None:
        """Port e_ownshipStateOut (5006) -> the orchestrator."""
        state.seq = self.ownship_seq
        self.ownship_seq += 1
        self.published.append(state)
        try:
            self._tx.sendto(state.to_json(),
                            (self.host, ports.UDP_PCS_TO_ORCH))
        except OSError:                            # [a: D3] teardown race
            pass

    # -- terrainElevationManager ----------------------------------------
    def fetch_ground_elevation(self, timeout: float = 0.5,
                               base_url: Optional[str] = None
                               ) -> Optional[float]:
        """TA action terrainElevationManager.

        [a: D4] FG httpd 8080, ground elevation at
            /json/position/ground-elev-m; "value may arrive as string".
        [a: D9] polled at 2 Hz by the caller's QTimer.
        """
        url = (base_url or f"http://{self.host}:{ports.HTTP_FG_HTTPD}") \
            + ports.FG_GROUND_ELEV_PATH
        try:
            import requests
            resp = requests.get(url, timeout=timeout)
            if resp.status_code != 200:
                return None
            payload = resp.json()
        except Exception:
            return None                # FlightGear not running: keep the
        return parse_ground_elevation(payload)      # last known terrain [b]

    # -- telemetryCaptureManager ----------------------------------------
    def start_capture(self, path: str) -> str:
        """[a] L2SYS-REQ-01.10b TELEMETRY CAPTURE; [a: D14] the 28-field
        header verbatim."""
        self.stop_capture()
        self._capture_file = open(path, "w", newline="", encoding="utf-8")
        self._capture_writer = csv.writer(self._capture_file)
        self._capture_writer.writerow(TELEMETRY_FIELDS)
        self._capture_file.flush()
        self.capture_path = path
        return path

    def capture_row(self, row: Dict[str, object]) -> None:
        if self._capture_writer is None:
            return
        self._capture_writer.writerow([row.get(f, "") for f in
                                       TELEMETRY_FIELDS])
        self._capture_file.flush()

    def stop_capture(self) -> Optional[str]:
        path = self.capture_path
        if self._capture_file is not None:
            try:
                self._capture_file.close()
            except OSError:
                pass
        self._capture_file = None
        self._capture_writer = None
        self.capture_path = None
        return path

    @property
    def capturing(self) -> bool:
        return self._capture_writer is not None

    def close(self) -> None:
        """[a: D3] OSError-tolerant teardown."""
        self.stop_capture()
        for sock in (self._hotas_sock, self._radar_sock, self._tx):
            if sock is None:
                continue
            try:
                sock.close()
            except OSError:
                pass
        self._hotas_sock = self._radar_sock = None


def parse_ground_elevation(payload) -> Optional[float]:
    """[a: D4] "(value may arrive as string)" - the FlightGear property
    JSON returns {"value": "123.4"} on some builds."""
    value = payload.get("value") if isinstance(payload, dict) else payload
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ==========================================================================
# Gauges  [a] 01.08c  (amendment A-2)
# ==========================================================================

class RoundGauge(QtWidgets.QWidget):
    """One round instrument. [a] L2SYS-REQ-01.08c; layout after the
    reference screenshot Desired_UIUX_capture.JPG (right-hand column)."""

    def __init__(self, name: str, unit: str, maximum: float = 100.0,
                 parent=None):
        super().__init__(parent)
        self.name = name
        self.unit = unit
        self.maximum = float(maximum)
        self.value = 0.0
        self.setMinimumSize(110, 110)

    def set_value(self, value: float) -> None:
        self.value = float(value)
        self.update()

    def paintEvent(self, event):                        # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = min(w, h) / 2.0 - 4.0
        p.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150), 1))
        p.setBrush(QtGui.QBrush(QtGui.QColor(8, 12, 8)))
        p.drawEllipse(QtCore.QPointF(w / 2.0, h / 2.0), r, r)
        frac = 0.0 if self.maximum <= 0 else min(1.0, abs(self.value)
                                                 / self.maximum)
        angle = math.radians(-90.0 + 300.0 * frac - 150.0 + 150.0)
        p.setPen(QtGui.QPen(QtGui.QColor(60, 230, 60), 2))
        p.drawLine(QtCore.QPointF(w / 2.0, h / 2.0),
                   QtCore.QPointF(w / 2.0 + r * 0.8 * math.cos(angle),
                                  h / 2.0 + r * 0.8 * math.sin(angle)))
        p.drawText(QtCore.QRectF(0, h / 2.0 - 26, w, 16),
                   QtCore.Qt.AlignCenter, self.name)
        p.drawText(QtCore.QRectF(0, h / 2.0 + 8, w, 16),
                   QtCore.Qt.AlignCenter, f"{self.value:.0f} {self.unit}")
        p.end()


class AttitudeIndicator(QtWidgets.QWidget):
    """Artificial horizon. [a] L2SYS-REQ-01.08c "Attitude"."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.roll_deg = 0.0
        self.pitch_deg = 0.0
        self.setMinimumSize(110, 110)

    def set_attitude(self, roll_deg: float, pitch_deg: float) -> None:
        self.roll_deg, self.pitch_deg = float(roll_deg), float(pitch_deg)
        self.update()

    def paintEvent(self, event):                        # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.translate(w / 2.0, h / 2.0)
        p.rotate(-self.roll_deg)
        offset = self.pitch_deg * 2.0
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(QtGui.QColor(30, 60, 140)))
        p.drawRect(QtCore.QRectF(-w, -h + offset, 2 * w, h))
        p.setBrush(QtGui.QBrush(QtGui.QColor(110, 70, 30)))
        p.drawRect(QtCore.QRectF(-w, offset, 2 * w, h))
        p.setPen(QtGui.QPen(QtGui.QColor(230, 230, 230), 2))
        p.drawLine(QtCore.QPointF(-w / 2.0, offset),
                   QtCore.QPointF(w / 2.0, offset))
        p.end()


# ==========================================================================
# D14 dark QSS
# ==========================================================================

PCS_QSS = """
QWidget { background-color: #141414; color: #cfcfcf;
          font-family: Consolas, 'DejaVu Sans Mono', monospace; }
QGroupBox { border: 1px solid #3d3d3d; margin-top: 9px; padding-top: 9px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #9fd39f; }
QPushButton { background-color: #262626; border: 1px solid #4a4a4a;
              padding: 5px; }
QPushButton:hover { background-color: #333333; }
QPushButton:disabled { color: #666666; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background-color: #0b0b0b; border: 1px solid #3d3d3d; }
"""


# ==========================================================================
# PCSMainWindow  (UserInterfaceManager)
# ==========================================================================

class PCSMainWindow(QtWidgets.QMainWindow):
    """part def UserInterfaceManager, assembled with BackgroundIOManager
    into ThreadFlightSim.

    [a] L2SYS-REQ-01.08: "The system shall provide a virtual cockpit
        console for the pilot, aka PCS or Pilot Control Station."
    [a: D14] the panel layout of the reference screenshot.
    """

    def __init__(self, physics: Optional[ThreadPhysics] = None,
                 io_manager: Optional[BackgroundIOManager] = None,
                 table: Optional[AirportTable] = None,
                 aircraft_dir: Optional[str] = None,
                 with_map: bool = False, start_timers: bool = True,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("F-16 PCS (Pilot Control Station)")
        self.setStyleSheet(PCS_QSS)                    # [a: D14] dark QSS
        self.table = table or DEFAULT_TABLE
        # [b] The window owns (and therefore tears down) only the thread
        # parts it created itself; an injected ThreadPhysics or
        # BackgroundIOManager belongs to the caller.
        self._owns_physics = physics is None
        self._owns_io = io_manager is None
        self.physics = physics if physics is not None else ThreadPhysics(
            table=self.table)
        self.io = io_manager if io_manager is not None else BackgroundIOManager()
        self.autopilot = Autopilot()
        # [b] FlightGear deployment parameters come from the environment
        # (FSIM_FG_*), resolved once at construction; an explicit
        # aircraft_dir argument still wins, for tests and for callers that
        # already know the path.
        self.fg_config = fg_config.resolve_flightgear()
        self.aircraft_dir = aircraft_dir or self.fg_config.aircraft_dir
        self.fg_process: Optional[subprocess.Popen] = None
        self.last_state: OwnshipState = self.physics.telemetry()
        self._attention_visible = False
        self.warning_state = ""
        self.map_window: Optional[ThreadMapWindow] = None

        self._build_ui()
        if with_map:
            self.map_window = ThreadMapWindow(start_fetcher=start_timers)
        self._build_timers(start_timers)
        self.update_cockpit_displays(self.last_state)
        self.set_freeze_gate()
        self.log("PCS READY - SIMULATION PAUSED")

    # ------------------------------------------------------------------
    # Layout  [a: D14]
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        # -- attention-getter strip [a: D14] / [a] 02.04 --------------
        self.attention_strip = QtWidgets.QLabel("")
        self.attention_strip.setAlignment(QtCore.Qt.AlignCenter)
        self.attention_strip.setMinimumHeight(30)
        self.attention_strip.setStyleSheet(
            "background-color:#0b0b0b; border:1px solid #3d3d3d;"
            "color:#ff3030; font-weight:bold; font-size:16px;")
        root.addWidget(self.attention_strip)

        middle = QtWidgets.QHBoxLayout()
        root.addLayout(middle, 1)

        # -- Virtual HOTAS, left [a: D14] / [a] 01.08b ----------------
        self.virtual_hotas = VirtualHotasWidget()
        middle.addWidget(self.virtual_hotas)

        centre_col = QtWidgets.QVBoxLayout()
        middle.addLayout(centre_col, 1)
        centre_col.addWidget(self._build_system_controls())
        centre_col.addWidget(self._build_navigation_controls())

        right_col = QtWidgets.QVBoxLayout()
        middle.addLayout(right_col)
        right_col.addWidget(self._build_mission_control())
        right_col.addWidget(self._build_ownship_state())

        middle.addWidget(self._build_gauges())

        root.addWidget(self._build_sim_control())

        # -- message window [a] 01.08c / [a: D14] ---------------------
        self.message_log = QtWidgets.QPlainTextEdit()
        self.message_log.setReadOnly(True)
        self.message_log.setMaximumHeight(110)
        root.addWidget(self.message_log)

        self._build_menu()

    def _build_system_controls(self) -> QtWidgets.QGroupBox:
        """[a] L2SYS-REQ-01.06: the PCS shall provide a System Controls
        Panel; 01.06b-e are its four toggle-buttons."""
        box = QtWidgets.QGroupBox("System Controls")
        grid = QtWidgets.QGridLayout(box)
        self.prk_brk_button = QtWidgets.QPushButton()      # 01.06c
        self.engine_button = QtWidgets.QPushButton()       # 01.06b
        self.air_brake_button = QtWidgets.QPushButton()    # 01.06d
        self.gear_button = QtWidgets.QPushButton()         # 01.06e
        self.prk_brk_button.clicked.connect(self.on_park_brake)
        self.engine_button.clicked.connect(self.on_engine)
        self.air_brake_button.clicked.connect(self.on_air_brake)
        self.gear_button.clicked.connect(self.on_gear)
        grid.addWidget(self.prk_brk_button, 0, 0)
        grid.addWidget(self.engine_button, 0, 1)
        grid.addWidget(self.air_brake_button, 1, 0)
        grid.addWidget(self.gear_button, 1, 1)
        self.system_controls_box = box
        self._refresh_system_buttons()
        return box

    def _build_navigation_controls(self) -> QtWidgets.QGroupBox:
        """[a] L2SYS-REQ-01.11: Navigation Controls Panel - Altitude (m),
        Speed (m/s), Heading (deg) + AP / AH / AT."""
        box = QtWidgets.QGroupBox("Navigation Controls")
        grid = QtWidgets.QGridLayout(box)
        self.alt_target = QtWidgets.QDoubleSpinBox()
        self.alt_target.setRange(-500.0, 20000.0)
        self.alt_target.setSuffix(" m")
        self.spd_target = QtWidgets.QDoubleSpinBox()
        self.spd_target.setRange(0.0, 900.0)
        self.spd_target.setSuffix(" m/s")
        self.hdg_target = QtWidgets.QDoubleSpinBox()
        self.hdg_target.setRange(0.0, 360.0)
        self.hdg_target.setSuffix(" deg")
        self.alt_target.valueChanged.connect(
            lambda v: setattr(self.autopilot, "target_alt_m", float(v)))
        self.spd_target.valueChanged.connect(
            lambda v: setattr(self.autopilot, "target_speed_mps", float(v)))
        self.hdg_target.valueChanged.connect(
            lambda v: setattr(self.autopilot, "target_heading_deg", float(v)))
        for row, (label, widget) in enumerate(
                (("Alt", self.alt_target), ("Spd", self.spd_target),
                 ("Hdg", self.hdg_target))):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            grid.addWidget(widget, row, 1)
        self.ap_button = QtWidgets.QPushButton("AP")
        self.ah_button = QtWidgets.QPushButton("AH")
        self.at_button = QtWidgets.QPushButton("AT")
        for b in (self.ap_button, self.ah_button, self.at_button):
            b.setCheckable(True)
        self.ap_button.clicked.connect(self.on_ap)
        self.ah_button.clicked.connect(self.on_ah)
        self.at_button.clicked.connect(self.on_at)
        grid.addWidget(self.ap_button, 3, 0)
        grid.addWidget(self.ah_button, 3, 1)
        grid.addWidget(self.at_button, 3, 2)
        self.navigation_controls_box = box
        return box

    def _build_mission_control(self) -> QtWidgets.QGroupBox:
        """[a: D14] "Mission Control (airport combo, freeze-gated)";
        [a] L2SYS-REQ-01.04 / 01.04b; TA action ssfSelectAirport."""
        box = QtWidgets.QGroupBox("Mission Control")
        layout = QtWidgets.QVBoxLayout(box)
        layout.addWidget(QtWidgets.QLabel("Location:"))
        self.airport_combo = QtWidgets.QComboBox()
        for ap in self.table.all():
            self.airport_combo.addItem(f"{ap.name} ({ap.icao})", ap.icao)
        index = self.airport_combo.findData(self.physics.airport.icao)
        if index >= 0:
            self.airport_combo.setCurrentIndex(index)
        self.airport_combo.currentIndexChanged.connect(self.on_airport_changed)
        layout.addWidget(self.airport_combo)
        self.mission_control_box = box
        return box

    def _build_ownship_state(self) -> QtWidgets.QGroupBox:
        """[a: D14] "tabulated Ownship State readouts (fixed-width,
        right-aligned)"."""
        box = QtWidgets.QGroupBox("Ownship State")
        form = QtWidgets.QFormLayout(box)
        self.state_labels: Dict[str, QtWidgets.QLabel] = {}
        for key, label in (("lat_deg", "Lat"), ("lon_deg", "Lon"),
                           ("alt_m", "Alt (m)"), ("agl_m", "AGL (m)"),
                           ("speed_mps", "Spd (m/s)"),
                           ("vspeed_mps", "V/S (m/s)"),
                           ("heading_deg", "Hdg (deg)"),
                           ("pitch_deg", "Pitch (deg)"),
                           ("roll_deg", "Roll (deg)"),
                           ("fuel_kg", "Fuel (kg)"),
                           ("rpm_pct", "RPM (%)"),
                           ("sim_time_s", "t_sim (s)")):
            value = QtWidgets.QLabel("0.000")
            value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            font = QtGui.QFont("Consolas")
            font.setStyleHint(QtGui.QFont.Monospace)
            value.setFont(font)
            self.state_labels[key] = value
            form.addRow(QtWidgets.QLabel(label), value)
        self.ownship_state_box = box
        return box

    def _build_gauges(self) -> QtWidgets.QGroupBox:
        """[a] L2SYS-REQ-01.08c (amendment A-2): Airspeed, Attitude, Fuel,
        Heading, RPM, HUD view, Attention Getters, Message Window."""
        box = QtWidgets.QGroupBox("Flight Instruments")
        col = QtWidgets.QVBoxLayout(box)
        self.attitude_gauge = AttitudeIndicator()
        self.heading_gauge = RoundGauge("Heading", "deg", 360.0)
        self.airspeed_gauge = RoundGauge("Airspeed", "m/s", 700.0)
        self.altitude_gauge = RoundGauge("Altitude", "m", 20000.0)
        self.fuel_gauge = RoundGauge("Fuel", "kg", 1500.0)
        self.rpm_gauge = RoundGauge("RPM", "%", 100.0)
        self.hud_label = QtWidgets.QLabel("HUD")     # HUD view
        self.hud_label.setAlignment(QtCore.Qt.AlignCenter)
        self.hud_label.setStyleSheet("color:#40ff40;")
        for widget in (self.attitude_gauge, self.heading_gauge,
                       self.airspeed_gauge, self.altitude_gauge,
                       self.fuel_gauge, self.rpm_gauge, self.hud_label):
            col.addWidget(widget)
        self.gauges_box = box
        return box

    def _build_sim_control(self) -> QtWidgets.QGroupBox:
        """[a] L2SYS-REQ-01.10 Sim Control Panel; [a] 01.10b the nine SSF;
        [a: D14] "Sim Control SSF button grid"."""
        box = QtWidgets.QGroupBox("Sim Control")
        grid = QtWidgets.QGridLayout(box)
        self.ssf_buttons: Dict[str, QtWidgets.QPushButton] = {}
        specs = [
            ("RUN SIM", self.ssf_run, False),
            ("PAUSE SIM", self.ssf_pause, False),
            ("RESET SIM", self.ssf_reset, True),
            ("LAUNCH 3D OTW", self.ssf_launch_otw, False),
            ("SET POSITION", self.ssf_set_position, True),
            ("Save Snapshot", self.ssf_save_snapshot, True),
            ("Load Snapshot", self.ssf_load_snapshot, True),
            ("FUEL FREEZE", self.ssf_fuel_freeze, False),
            ("Telemetry Capture", self.ssf_telemetry_capture, False),
        ]
        #: [a: D14] which SSF share the one freeze gate; the caveats in the
        #: ssf* doc comments of the TA name RESET / SET POSITION / SAVE /
        #: LOAD, plus 01.04b for the airport combo.
        self.freeze_gated = {name for name, _, gated in specs if gated}
        for i, (name, slot, _gated) in enumerate(specs):
            button = QtWidgets.QPushButton(name)
            # [b] DEFECT FIX: QPushButton.clicked carries a `checked` bool,
            # and PyQt binds it to the slot's FIRST optional parameter. A
            # direct connect therefore called ssf_set_position(lat_deg=False)
            # -> latitude 0.0, and ssf_save_snapshot(path=False). The lambda
            # swallows the signal argument so every SSF button invokes its
            # action with NO arguments, i.e. with the panel's own values.
            button.clicked.connect(lambda _checked=False, fn=slot: fn())
            grid.addWidget(button, i // 5, i % 5)
            self.ssf_buttons[name] = button
        self.sim_control_box = box
        return box

    def _build_menu(self) -> None:
        """[a: D14] "SSF menu + button grid sharing one freeze gate
        (buttons trigger the same actions)"."""
        menu = self.menuBar().addMenu("&SSF")
        self.ssf_actions: Dict[str, QtWidgets.QAction] = {}
        for name, button in self.ssf_buttons.items():
            action = QtWidgets.QAction(name, self)
            action.triggered.connect(button.click)   # the SAME action
            menu.addAction(action)
            self.ssf_actions[name] = action

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------
    def _build_timers(self, start: bool) -> None:
        self.io_timer = QtCore.QTimer(self)              # bridgeUdpData
        self.io_timer.setInterval(10)
        self.io_timer.timeout.connect(self.on_io_tick)

        self.publish_timer = QtCore.QTimer(self)         # e_ownshipStateOut
        self.publish_timer.setInterval(
            int(1000 / ports.OWNSHIP_PUBLISH_HZ))
        self.publish_timer.timeout.connect(self.on_publish_tick)

        self.terrain_timer = QtCore.QTimer(self)         # [a: D9] 2 Hz
        self.terrain_timer.setInterval(int(1000 / ports.TERRAIN_POLL_HZ))
        self.terrain_timer.timeout.connect(self.on_terrain_tick)

        self.display_timer = QtCore.QTimer(self)         # updateCockpitDisplays
        self.display_timer.setInterval(50)
        self.display_timer.timeout.connect(self.on_display_tick)

        self.attention_timer = QtCore.QTimer(self)       # [a] 02.04 flash
        self.attention_timer.setInterval(ATTENTION_FLASH_MS)
        self.attention_timer.timeout.connect(self.on_attention_tick)

        if start:
            for timer in (self.io_timer, self.publish_timer,
                          self.terrain_timer, self.display_timer,
                          self.attention_timer):
                timer.start()

    # ------------------------------------------------------------------
    # Ticks
    # ------------------------------------------------------------------
    def on_io_tick(self) -> None:
        """bridgeUdpData + the D10 precedence arbitration + control
        hand-off to the physics thread (port i_HotasControlOut)."""
        self.io.poll()
        now = time.monotonic()
        physical = self.io.physical_active(now)
        self.virtual_hotas.set_physical_active(physical)   # [a: D10]
        if physical and self.io.last_physical is not None:
            controls = self.io.last_physical
            self.virtual_hotas.mirror_physical(controls, now)  # [a: D10]
        else:
            controls = self.virtual_hotas.controls()           # [a] 01.08b
        commanded = self.autopilot.apply(self.last_state, controls)
        self.physics.set_controls(commanded)
        self._sync_system_buttons_from_physics()

    def on_publish_tick(self) -> None:
        state = self.physics.telemetry()
        self.last_state = state
        self.io.publish_ownship(state)                # port e_ownshipStateOut
        if self.map_window is not None:
            self.map_window.i_OwnStateIn(state)       # port i_OwnStateOut
            self.map_window.i_BeaconDataIn(self.io.beacons.values())
        if self.io.capturing:
            self.io.capture_row(self.telemetry_row(state))

    def on_terrain_tick(self) -> None:
        """[a: D9] poll FG ground elevation at 2 Hz and feed the FDM."""
        elevation = self.io.fetch_ground_elevation()
        if elevation is not None:
            self.physics.set_terrain_elevation_m(elevation)

    def on_display_tick(self) -> None:
        self.update_cockpit_displays(self.physics.telemetry())

    def on_attention_tick(self) -> None:
        """[a] L2SYS-REQ-02.04: "Attention Getter shall flash
        intermittently while warning condition persists."""
        if not self.warning_state:
            self.attention_strip.setText("")
            self._attention_visible = False
            return
        self._attention_visible = not self._attention_visible
        self.attention_strip.setText(
            self.warning_state if self._attention_visible else "")

    # ------------------------------------------------------------------
    # updateCockpitDisplays
    # ------------------------------------------------------------------
    def update_cockpit_displays(self, state: OwnshipState) -> None:
        """TA action updateCockpitDisplays. [a] 01.08c, 02.04, D14."""
        self.last_state = state
        for key, label in self.state_labels.items():
            label.setText(f"{getattr(state, key):12.3f}")
        self.attitude_gauge.set_attitude(state.roll_deg, state.pitch_deg)
        self.heading_gauge.set_value(state.heading_deg)
        self.airspeed_gauge.set_value(state.speed_mps)
        self.altitude_gauge.set_value(state.alt_m)
        self.fuel_gauge.set_value(state.fuel_kg)
        self.rpm_gauge.set_value(state.rpm_pct)
        self.hud_label.setText(
            f"HUD  {state.speed_mps:6.1f} m/s   {state.alt_m:8.1f} m   "
            f"{state.heading_deg:5.1f} deg")
        # [a] 02.04 ground-proximity attention getter
        self.warning_state = "PULL UP" if ground_proximity_warning(state) \
            else ""
        if not self.warning_state:
            self.attention_strip.setText("")

    def log(self, message: str) -> None:
        """The message window. [a] 01.08c "Message Window"; [a: D14]."""
        self.message_log.appendPlainText(message)

    # ------------------------------------------------------------------
    # System Controls slots  [a] 01.06b-e
    # ------------------------------------------------------------------
    def _refresh_system_buttons(self) -> None:
        """[a: D14] "System Controls (state-colored PRK BRK / ENGINE /
        AIR BRAKE / GEAR)"."""
        def paint(button, text, on, on_colour="#d07000", off_colour="#c02020"):
            button.setText(text)
            button.setStyleSheet(
                f"background-color:{on_colour if on else off_colour};"
                "color:#ffffff; font-weight:bold;")

        paint(self.prk_brk_button,
              f"PRK BRK: {'ON' if self.physics.park_brake else 'OFF'}",
              self.physics.park_brake)
        paint(self.engine_button,
              f"ENGINE: {'ON' if self.physics.engine_on else 'OFF'}",
              self.physics.engine_on, on_colour="#208020")
        paint(self.air_brake_button,
              f"AIR BRAKE: {'ON' if self.physics.air_brake else 'OFF'}",
              self.physics.air_brake, on_colour="#d07000",
              off_colour="#303030")
        paint(self.gear_button,
              f"GEAR: {'DOWN' if self.physics.gear_down else 'UP'}",
              self.physics.gear_down, on_colour="#208020",
              off_colour="#303030")

    def _sync_system_buttons_from_physics(self) -> None:
        """[a: D7] the HOTAS buttons can toggle gear/air brake too, so the
        panel must follow the physics state, not only its own clicks."""
        self._refresh_system_buttons()

    def on_engine(self) -> None:
        """[a] L2SYS-REQ-01.06b ENGINE toggle-button (ON/OFF)."""
        want = not self.physics.engine_on
        if not self.physics.set_engine(want):
            self.log("ENGINE START REFUSED - THROTTLE ABOVE IDLE")  # [a: D8]
        else:
            self.log(f"ENGINE: {'ON' if want else 'OFF'}")
        self._refresh_system_buttons()

    def on_park_brake(self) -> None:
        """[a] L2SYS-REQ-01.06c PARK BRAKE toggle-button (ON/OFF)."""
        self.physics.set_park_brake(not self.physics.park_brake)
        self.log(f"PRK BRK: {'ON' if self.physics.park_brake else 'OFF'}")
        self._refresh_system_buttons()

    def on_air_brake(self) -> None:
        """[a] L2SYS-REQ-01.06d AIR BRAKE toggle-button (ON/OFF)."""
        self.physics.set_air_brake(not self.physics.air_brake)
        self.log(f"AIR BRAKE: {'ON' if self.physics.air_brake else 'OFF'}")
        self._refresh_system_buttons()

    def on_gear(self) -> None:
        """[a] L2SYS-REQ-01.06e LDG GEAR toggle-button (UP/DOWN)."""
        self.physics.set_gear_down(not self.physics.gear_down)
        self.log(f"GEAR: {'DOWN' if self.physics.gear_down else 'UP'}")
        self._refresh_system_buttons()

    # ------------------------------------------------------------------
    # Navigation slots  [a] 01.11 / D11
    # ------------------------------------------------------------------
    def on_ap(self) -> None:
        """[a] 01.11 AP (heading hold); [a: D11] sync-on-engage."""
        self.autopilot.engage_ap(self.last_state, self.ap_button.isChecked())
        if self.ap_button.isChecked():
            self.hdg_target.setValue(self.autopilot.target_heading_deg)
        self.log(f"AP {'ENGAGED' if self.ap_button.isChecked() else 'OFF'}")

    def on_ah(self) -> None:
        """[a] 01.11 AH (altitude hold); [a: D11] sync-on-engage."""
        self.autopilot.engage_ah(self.last_state, self.ah_button.isChecked())
        if self.ah_button.isChecked():
            self.alt_target.setValue(self.autopilot.target_alt_m)
        self.log(f"AH {'ENGAGED' if self.ah_button.isChecked() else 'OFF'}")

    def on_at(self) -> None:
        """[a] 01.11 AT (autothrottle); [a: D11] sync-on-engage."""
        self.autopilot.engage_at(self.last_state, self.at_button.isChecked())
        if self.at_button.isChecked():
            self.spd_target.setValue(self.autopilot.target_speed_mps)
        self.log(f"AT {'ENGAGED' if self.at_button.isChecked() else 'OFF'}")

    # ------------------------------------------------------------------
    # Mission Control  [a] 01.04 / 01.04b
    # ------------------------------------------------------------------
    def on_airport_changed(self, index: int) -> None:
        """TA action ssfSelectAirport. [a] L2SYS-REQ-01.04."""
        icao = self.airport_combo.itemData(index)
        if not icao:
            return
        self.physics.select_airport(icao)
        self.log(f"AIRPORT: {icao}")

    # ------------------------------------------------------------------
    # Freeze gate  [a] 01.04b + the ssf* caveats of the TA
    # ------------------------------------------------------------------
    def set_freeze_gate(self) -> None:
        """[a] L2SYS-REQ-01.04b: "The airport selection shall be
        changeable only during flight-freeze."
        [a] TA ssfResetSimulation / ssfSetOwnPosition / ssfSaveSnapshot /
            ssfLoadSnapshot / ssfSelectAirport doc caveats: "can be applied
            only during a flight-freeze condition".
        [a: D14] the menu and the button grid share this one gate.
        """
        frozen = self.physics.paused
        self.airport_combo.setEnabled(frozen)
        for name in self.freeze_gated:
            self.ssf_buttons[name].setEnabled(frozen)
            self.ssf_actions[name].setEnabled(frozen)

    # ------------------------------------------------------------------
    # SSF  [a] 01.10b   (TA action manageSSF)
    # ------------------------------------------------------------------
    def ssf_run(self) -> None:
        """RUN SIM. [a] TA ssfStartSimulation; L2SYS-REQ-01.10b."""
        self.physics.ssf_run()
        if not self.physics.is_alive():
            self.physics.start()
        self.set_freeze_gate()
        self.log("RUN SIM")

    def ssf_pause(self) -> None:
        """PAUSE SIM (flight freeze). [a] TA ssfStopSimulation."""
        self.physics.ssf_pause()
        self.set_freeze_gate()
        self.log("PAUSE SIM - FLIGHT FREEZE")

    def ssf_reset(self) -> None:
        """RESET SIM. [a] TA ssfResetSimulation; [a: D8] clean slate with
        the AP modes disengaged."""
        if not self.physics.paused:
            self.log("RESET REFUSED - REQUIRES FLIGHT FREEZE")
            return
        self.autopilot.disengage_all()                 # [a: D8]
        for button in (self.ap_button, self.ah_button, self.at_button):
            button.setChecked(False)
        state = self.physics.ssf_reset()
        self.io.publish_ownship(state)                 # published while frozen
        self.virtual_hotas.throttle.setValue(0)
        self.update_cockpit_displays(state)
        self._refresh_system_buttons()
        self.log("RESET SIM - INITIAL CONDITIONS RELOADED")

    def ssf_launch_otw(self) -> List[str]:
        """LAUNCH 3D OTW. [a] TA ssfLaunchOtwView; [a: D6] command line.

        [b] The executable path is resolved from the environment
        (flightsim.common.config). If FlightGear cannot be located the SSF
        reports exactly what to set and does NOT attempt the launch -- a
        missing installation is a deployment problem to be told about, not
        an exception to propagate into the Qt event loop.
        """
        icao = self.airport_combo.currentData() or self.physics.airport.icao
        # re-resolve on every launch so an operator can fix the environment
        # and retry without restarting the PCS [b]
        self.fg_config = fg_config.resolve_flightgear()
        command = build_flightgear_command(
            icao, self.aircraft_dir,
            executable=self.fg_config.executable,
            fg_root=self.fg_config.fg_root)

        # [b] Severity matters: only a missing EXECUTABLE aborts. A missing
        # --fg-aircraft directory is reported and the launch proceeds --
        # FlightGear starts and falls back to a default aircraft, which is
        # far better than refusing to show the operator anything.
        blocking = self.fg_config.blocking_problems()
        if blocking:
            self.log("FLIGHTGEAR LAUNCH ABORTED - NOT CONFIGURED:")
            for problem in blocking:
                self.log(f"  - {problem}")
            self.log("  Run: python -m flightsim.common.config")
            return command
        for warning in self.fg_config.warnings():
            self.log(f"FLIGHTGEAR WARNING: {warning}")

        self.log(f"LAUNCHING FLIGHTGEAR 3D ENGINE AT {icao}...")
        self.log(f"  {self.fg_config.executable}")
        try:
            self.fg_process = subprocess.Popen(command)
        except Exception as exc:              # [b] never reaches the event loop
            self.log(f"FLIGHTGEAR LAUNCH FAILED: {exc}")
            self.log(f"  command: {' '.join(command)}")
            self.log(f"  set {fg_config.ENV_FG_EXECUTABLE} to the full path "
                     f"of fgfs.exe (see fsim_env.bat)")
        return command

    def ssf_set_position(self, lat_deg: Optional[float] = None,
                         lon_deg: Optional[float] = None,
                         alt_m: Optional[float] = None,
                         speed_mps: Optional[float] = None,
                         heading_deg: Optional[float] = None) -> None:
        """SET POSITION. [a] TA ssfSetOwnPosition; L2SYS-REQ-01.10b names
        "Lat, Lon (deg), Altitude (m), Speed (m/s), Heading (deg)"."""
        if not self.physics.paused:
            self.log("SET POSITION REFUSED - REQUIRES FLIGHT FREEZE")
            return
        state = self.physics.ssf_set_position(
            self.last_state.lat_deg if lat_deg is None else lat_deg,
            self.last_state.lon_deg if lon_deg is None else lon_deg,
            self.alt_target.value() if alt_m is None else alt_m,
            self.spd_target.value() if speed_mps is None else speed_mps,
            self.hdg_target.value() if heading_deg is None else heading_deg)
        self.update_cockpit_displays(state)
        self._refresh_system_buttons()
        self.log("SET POSITION APPLIED")

    def snapshot(self) -> Dict[str, object]:
        """[a] TA ssfSaveSnapshot: "save all state-variables of the
        simulation, incl: physics, beacon list, map view, otw view"."""
        return {
            "physics": self.physics.snapshot_state(),
            "beacons": [b.to_dict() for b in self.io.beacons.values()],
            "map_zoom": (self.map_window.mapGuiWorker.zoom
                         if self.map_window else None),
            "autopilot": {
                "target_alt_m": self.autopilot.target_alt_m,
                "target_speed_mps": self.autopilot.target_speed_mps,
                "target_heading_deg": self.autopilot.target_heading_deg,
                "ap_engaged": self.autopilot.ap_engaged,
                "ah_engaged": self.autopilot.ah_engaged,
                "at_engaged": self.autopilot.at_engaged,
            },
            "airport": self.physics.airport.icao,
            "otw_running": self.fg_process is not None,
        }

    def restore_snapshot(self, data: Dict[str, object]) -> None:
        """[a] TA ssfLoadSnapshot."""
        import copy
        data = copy.deepcopy(data)                    # [a] R-8 no aliasing
        state = self.physics.restore_state(data["physics"])  # type: ignore
        ap = data.get("autopilot", {})                # type: ignore[assignment]
        self.autopilot.target_alt_m = float(ap.get("target_alt_m", 0.0))
        self.autopilot.target_speed_mps = float(ap.get("target_speed_mps", 0.0))
        self.autopilot.target_heading_deg = float(
            ap.get("target_heading_deg", 0.0))
        self.autopilot.ap_engaged = bool(ap.get("ap_engaged", False))
        self.autopilot.ah_engaged = bool(ap.get("ah_engaged", False))
        self.autopilot.at_engaged = bool(ap.get("at_engaged", False))
        self.ap_button.setChecked(self.autopilot.ap_engaged)
        self.ah_button.setChecked(self.autopilot.ah_engaged)
        self.at_button.setChecked(self.autopilot.at_engaged)
        self.alt_target.setValue(self.autopilot.target_alt_m)
        self.spd_target.setValue(self.autopilot.target_speed_mps)
        self.hdg_target.setValue(self.autopilot.target_heading_deg)
        beacons = data.get("beacons", [])             # type: ignore[assignment]
        now = time.monotonic()
        for raw in beacons:
            self.io.beacons.apply(RadarBeacon(**raw), now)
        if self.map_window is not None and data.get("map_zoom"):
            self.map_window.mapGuiWorker.zoom = int(data["map_zoom"])
        self.update_cockpit_displays(state)
        self._refresh_system_buttons()

    def ssf_save_snapshot(self, path: Optional[str] = None) -> Optional[str]:
        """SAVE SNAPSHOT. [a] L2SYS-REQ-01.10b."""
        if not self.physics.paused:
            self.log("SAVE SNAPSHOT REFUSED - REQUIRES FLIGHT FREEZE")
            return None
        if path is None:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Snapshot", "snapshot.json", "JSON (*.json)")
        if not path:
            return None
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.snapshot(), fh, indent=1)
        self.log(f"SNAPSHOT SAVED: {os.path.basename(path)}")
        return path

    def ssf_load_snapshot(self, path: Optional[str] = None) -> Optional[str]:
        """LOAD SNAPSHOT. [a] L2SYS-REQ-01.10b."""
        if not self.physics.paused:
            self.log("LOAD SNAPSHOT REFUSED - REQUIRES FLIGHT FREEZE")
            return None
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Load Snapshot", "", "JSON (*.json)")
        if not path:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            self.restore_snapshot(json.load(fh))
        self.log(f"SNAPSHOT LOADED: {os.path.basename(path)}")
        return path

    def ssf_fuel_freeze(self) -> bool:
        """FUEL FREEZE. [a] TA ssfFreezeFuelConsupmtion; 01.10b."""
        self.physics.set_fuel_freeze(not self.physics.fuel_freeze)
        self.log(f"FUEL FREEZE: "
                 f"{'ON' if self.physics.fuel_freeze else 'OFF'}")
        return self.physics.fuel_freeze

    def telemetry_row(self, state: OwnshipState) -> Dict[str, object]:
        """[a: D14] one row of the 28-field telemetry CSV."""
        fdm = self.physics._fdm
        return {
            "wall_time_iso": _dt.datetime.now().isoformat(timespec="milliseconds"),
            "sim_time_s": f"{state.sim_time_s:.4f}",
            "model": self.physics.aircraft,
            "lat_deg": f"{state.lat_deg:.7f}",
            "lon_deg": f"{state.lon_deg:.7f}",
            "alt_m": f"{state.alt_m:.2f}",
            "agl_m": f"{state.agl_m:.2f}",
            "v_speed_mps": f"{state.vspeed_mps:.3f}",
            "speed_mps": f"{state.speed_mps:.3f}",
            "heading_deg": f"{state.heading_deg:.2f}",
            "target_heading_deg": f"{self.autopilot.target_heading_deg:.2f}",
            "pitch_deg": f"{state.pitch_deg:.2f}",
            "pitch_rate_dps": f"{state.pitch_rate_dps:.3f}",
            "roll_deg": f"{state.roll_deg:.2f}",
            "target_alt_m": f"{self.autopilot.target_alt_m:.2f}",
            "target_speed_mps": f"{self.autopilot.target_speed_mps:.2f}",
            "aileron_cmd": f"{fdm['fcs/aileron-cmd-norm']:.4f}",
            "elevator_cmd": f"{fdm['fcs/elevator-cmd-norm']:.4f}",
            "rudder_cmd": f"{fdm['fcs/rudder-cmd-norm']:.4f}",
            "throttle_cmd": f"{fdm['fcs/throttle-cmd-norm']:.4f}",
            "gear_cmd": f"{fdm['gear/gear-cmd-norm']:.2f}",
            "trim_pitch": f"{fdm['fcs/pitch-trim-cmd-norm']:.4f}",
            "trim_roll": f"{fdm['fcs/roll-trim-cmd-norm']:.4f}",
            "trim_yaw": f"{fdm['fcs/yaw-trim-cmd-norm']:.4f}",
            "ap_engaged": int(self.autopilot.ap_engaged),
            "ah_engaged": int(self.autopilot.ah_engaged),
            "at_engaged": int(self.autopilot.at_engaged),
            "warning_state": self.warning_state,
        }

    def ssf_telemetry_capture(self, path: Optional[str] = None
                              ) -> Optional[str]:
        """TELEMETRY CAPTURE. [a] TA ssfLogTelemetry; 01.10b; [a: D14]."""
        if self.io.capturing:
            stopped = self.io.stop_capture()
            self.log(f"TELEMETRY CAPTURE STOPPED: "
                     f"{os.path.basename(stopped or '')}")
            return stopped
        if path is None:
            path = time.strftime("telemetry_%Y%m%d_%H%M%S.csv")
        self.io.start_capture(path)
        self.log(f"TELEMETRY CAPTURE STARTED: {os.path.basename(path)}")
        return path

    # ------------------------------------------------------------------
    def closeEvent(self, event):                        # noqa: N802
        self.stop()
        super().closeEvent(event)

    def stop(self) -> None:
        """[a: D15] full teardown; [a: D3] OSError tolerant."""
        for name in ("io_timer", "publish_timer", "terrain_timer",
                     "display_timer", "attention_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        if self.map_window is not None:
            self.map_window.stop()
        if self._owns_physics and self.physics is not None:
            self.physics.stop()
        if self._owns_io and self.io is not None:
            self.io.close()


def main(argv=None) -> int:
    import sys
    app = QtWidgets.QApplication(sys.argv if argv is None else list(argv))
    window = PCSMainWindow(with_map=True)
    window.resize(1180, 900)
    window.show()
    if window.map_window is not None:
        window.map_window.show()
    return app.exec_()


__all__ = ["PCSMainWindow", "BackgroundIOManager", "Autopilot",
           "TELEMETRY_FIELDS", "build_flightgear_command",
           "ground_proximity_warning", "parse_ground_elevation",
           "FG_AIRCRAFT_MODEL"]
