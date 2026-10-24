"""
flightsim/run_sem.py
====================
Synthetic Environment Manager (SEM): radar / EW simulation and the
instructor's scenario GUI.

TRACEABILITY
------------
[a] Implements part def SyntheticEnvironmentMgr (fsim_TA_SEM.sysml), whose
    doc comment names this file: "Radar/EW simulation (run_sem.py)".
    Specializes FlightSimulator_LogicalArchitecture::SyntheticEnvironment.
[a] TA actions of threadMainGui -> code:
        connectToSim           -> connect_to_sim()
        manageEwScenario       -> the Define Emitter Beacon group
          manageEwBeacon       -> add_beacon() / update_beacon() /
                                  remove_beacon()
          controlOwnshipRcs    -> set_ownship_rcs()
        updateSEUI             -> _refresh_roster()
        calcBeaconOwnshipRange -> slant_range_m()
        calcRadarEquation      -> calc_radar_equation()  (doc comment:
              "evaluate monostatic radar equation per beacon: emitter params
               (Pt, G, f, Pmin) + ownship RCS + slant range -> detection
               status and effective max detection range (published in the
               beacon msg)")
[a] Ports: e_ownshipStateIn 127.0.0.1:5007, e_radarDetectionsOut
    127.0.0.1:6007 (fsim_TA_SEM.sysml) [a: D4].
[a] Runtime: PythonRuntime version "3.14" (fsim_TA_SEM.sysml) -> .venv_fsim
    [a: D5].

REQUIREMENTS IMPLEMENTED HERE
-----------------------------
[a] L2SYS-REQ-03.01 - the system shall comprise a Synthetic Environment.
[a] L2SYS-REQ-03.02 - GUI to spawn and classify (FRIEND, FOE, UNKNOWN)
                      radar emitters with defined maximum ranges.
[a] L2SYS-REQ-03.03 - per-beacon emitter parameters of the radar equation
                      (transmit power, antenna gain, frequency, receiver
                      sensitivity).
[a] L2SYS-REQ-03.04 - ownship radar cross section as a scenario parameter.
[a] L2SYS-REQ-03.05 - compute each beacon's detection status by evaluating
                      the monostatic radar equation against the received
                      ownship state.

DECISIONS IMPLEMENTED HERE
--------------------------
[a: D1]  RadarBeacon wire message.
[a: D2]  event-driven transmission (only the changed beacon), tombstone
         deletion, 2.0 s background refresh.
[a: D12] R_max = (Pt*G^2*lambda^2*sigma / ((4*pi)^3 * P_min))^(1/4),
         published in max_range_m; detection = slant <= R_max; per-beacon
         editable Pt/G/f/P_min (defaults 100 kW / 35 dBi / 10 GHz /
         -90 dBm); ownship RCS as a scenario spinbox (default 5 m^2 ->
         approx. 39 km); an RCS change recomputes and retransmits every
         beacon; beacons without emitter params keep their explicit range.
[a: D14] dark QSS, consistent with the PCS.
"""

from __future__ import annotations

import math
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from flightsim.common import ports
from flightsim.common.messages import (
    BEACON_REFRESH_S, CLASSIFICATIONS, MessageError, OwnshipState, RadarBeacon,
)

# --------------------------------------------------------------------------
# Physical constants and D12 defaults
# --------------------------------------------------------------------------

SPEED_OF_LIGHT_MPS = 299_792_458.0
EARTH_RADIUS_M = 6_371_000.0

#: [a: D12] "defaults 100 kW / 35 dBi / 10 GHz / -90 dBm"
DEFAULT_PT_W = 100_000.0            # 100 kW transmit power
DEFAULT_GAIN_DB = 35.0              # 35 dBi antenna gain
DEFAULT_FREQ_HZ = 10.0e9            # 10 GHz
DEFAULT_PMIN_W = 1.0e-12            # -90 dBm = 1e-12 W receiver sensitivity

#: [a: D12] "ownship RCS as scenario spinbox (default 5 m^2 -> approx 39 km)"
DEFAULT_OWNSHIP_RCS_M2 = 5.0

#: [b] System loss factor L. Not named by D12 (whose formula is lossless),
#: but present in the reference GUI screenshot Desired_UIUX_capture.JPG as
#: "Loss (L)". Default 1.0, so the implementation reduces exactly to the
#: D12 formula unless the instructor changes it.
DEFAULT_LOSS = 1.0


def dbi_to_linear(gain_db: float) -> float:
    """[b] Antenna gain is entered in dBi (03.03 "antenna gain"); the radar
    equation needs the linear ratio."""
    return 10.0 ** (float(gain_db) / 10.0)


def wavelength_m(freq_hz: float) -> float:
    """[b] lambda = c / f; 03.03 names frequency, D12's formula uses
    lambda."""
    return SPEED_OF_LIGHT_MPS / float(freq_hz)


def calc_radar_equation(pt_w: float = DEFAULT_PT_W,
                        gain_db: float = DEFAULT_GAIN_DB,
                        freq_hz: float = DEFAULT_FREQ_HZ,
                        rcs_m2: float = DEFAULT_OWNSHIP_RCS_M2,
                        pmin_w: float = DEFAULT_PMIN_W,
                        loss: float = DEFAULT_LOSS) -> float:
    """TA action calcRadarEquation -> maximum detection range, metres.

    [a] L2SYS-REQ-03.05: "compute each beacon's detection status by
        evaluating the monostatic radar equation against the received
        ownship state."
    [a: D12] R_max = (Pt * G^2 * lambda^2 * sigma / ((4*pi)^3 * P_min))^(1/4)
    [b] the optional loss factor L divides the numerator; with the default
        L = 1.0 this is exactly the D12 expression.
    """
    g = dbi_to_linear(gain_db)
    lam = wavelength_m(freq_hz)
    numerator = float(pt_w) * (g ** 2) * (lam ** 2) * float(rcs_m2)
    denominator = ((4.0 * math.pi) ** 3) * float(pmin_w) * float(loss)
    if denominator <= 0.0 or numerator <= 0.0:
        return 0.0
    return (numerator / denominator) ** 0.25


def slant_range_m(own: OwnshipState, lat_deg: float, lon_deg: float,
                  alt_m: float) -> float:
    """TA action calcBeaconOwnshipRange.

    [b] great-circle ground distance (spherical earth) combined with the
    altitude difference; the model names the action but not the geodesy.
    """
    phi1, phi2 = math.radians(own.lat_deg), math.radians(float(lat_deg))
    dphi = phi2 - phi1
    dlam = math.radians(float(lon_deg) - own.lon_deg)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    ground = 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))
    dh = own.alt_m - float(alt_m)
    return math.hypot(ground, dh)


def offset_position(lat_deg: float, lon_deg: float, range_m: float,
                    bearing_deg: float):
    """[b] Place a beacon at (range, bearing) from a reference point, which
    is how the reference GUI's 'Initial Range' / 'Initial Theta' fields
    behave."""
    phi1 = math.radians(lat_deg)
    lam1 = math.radians(lon_deg)
    theta = math.radians(bearing_deg)
    delta = float(range_m) / EARTH_RADIUS_M
    phi2 = math.asin(math.sin(phi1) * math.cos(delta)
                     + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lam2 = lam1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2))
    return math.degrees(phi2), (math.degrees(lam2) + 540.0) % 360.0 - 180.0


# ==========================================================================
# Scenario model
# ==========================================================================

@dataclass
class BeaconRecord:
    """One emitter in the instructor's scenario.

    [a] L2SYS-REQ-03.02 (classification + defined maximum range),
        L2SYS-REQ-03.03 (per-beacon Pt, G, f, Pmin).
    [a: D12] "beacons without emitter params keep their explicit range
        (test compatibility)" -> explicit_range_m is not None for those.
    """

    beacon_id: int
    classification: str = "UNKNOWN"
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_m: float = 0.0
    pt_w: Optional[float] = DEFAULT_PT_W
    gain_db: Optional[float] = DEFAULT_GAIN_DB
    freq_hz: Optional[float] = DEFAULT_FREQ_HZ
    pmin_w: Optional[float] = DEFAULT_PMIN_W
    loss: float = DEFAULT_LOSS
    explicit_range_m: Optional[float] = None
    slant_range_m: float = 0.0
    detected: bool = False

    @property
    def has_emitter_params(self) -> bool:
        """[a: D12] a beacon whose emitter parameters are all present has
        its range computed; otherwise the explicit range is kept."""
        return (self.explicit_range_m is None
                and None not in (self.pt_w, self.gain_db, self.freq_hz,
                                 self.pmin_w))

    def max_range_m(self, ownship_rcs_m2: float) -> float:
        """[a: D12] the effective maximum detection range published in the
        RadarBeacon.max_range_m field (wire unchanged)."""
        if not self.has_emitter_params:
            return float(self.explicit_range_m or 0.0)
        return calc_radar_equation(self.pt_w, self.gain_db, self.freq_hz,
                                   ownship_rcs_m2, self.pmin_w, self.loss)

    def to_message(self, ownship_rcs_m2: float) -> RadarBeacon:
        """[a: D1] the wire representation of this beacon."""
        return RadarBeacon(
            beacon_id=self.beacon_id, classification=self.classification,
            lat_deg=self.lat_deg, lon_deg=self.lon_deg, alt_m=self.alt_m,
            max_range_m=self.max_range_m(ownship_rcs_m2),
            detected=self.detected, slant_range_m=self.slant_range_m,
            active=True)


class SemCore:
    """Headless SEM logic: scenario state, radar evaluation and the D2
    transmission protocol. The Qt window drives this object.

    [a] L2SYS-REQ-03.01: the system shall comprise a Synthetic Environment.
    """

    def __init__(self, host: str = ports.LOCALHOST,
                 rx_port: int = ports.UDP_ORCH_TO_SEM,
                 tx_port: int = ports.UDP_SEM_TO_ORCH,
                 bind: bool = True):
        self.host = host
        self.rx_port = int(rx_port)          # e_ownshipStateIn  (5007)
        self.tx_port = int(tx_port)          # e_radarDetectionsOut (6007)
        self.connected = False
        self.ownship = OwnshipState()
        self.ownship_rcs_m2 = DEFAULT_OWNSHIP_RCS_M2   # [a: D12] / 03.04
        self.beacons: Dict[int, BeaconRecord] = {}
        self._next_id = 1
        self.transmitted: List[RadarBeacon] = []       # diagnostics/tests
        self._rx: Optional[socket.socket] = None
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if bind:
            self._bind_rx()

    # -- connectToSim ----------------------------------------------------
    def _bind_rx(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.rx_port))
        sock.setblocking(False)
        self._rx = sock

    def connect_to_sim(self) -> bool:
        """TA action connectToSim; the CONNECT TO SIM button of the
        reference GUI. [a] L2SYS-REQ-03.01."""
        if self._rx is None:
            self._bind_rx()
        self.connected = True
        return self.connected

    def poll_ownship(self) -> Optional[OwnshipState]:
        """Drain e_ownshipStateIn (5007) and keep the newest state.

        [a] L2SYS-REQ-03.05 "against the received ownship state".
        """
        if self._rx is None:
            return None
        newest = None
        while True:
            try:
                data, _ = self._rx.recvfrom(65535)
            except (BlockingIOError, OSError):
                break
            try:
                newest = OwnshipState.from_json(data)
            except MessageError:
                continue                       # [a: D1] reject malformed
        if newest is not None:
            self.ownship = newest
            self.evaluate_all()
        return newest

    # -- manageEwBeacon --------------------------------------------------
    def add_beacon(self, classification: str = "UNKNOWN",
                   lat_deg: Optional[float] = None,
                   lon_deg: Optional[float] = None, alt_m: float = 0.0,
                   range_m: Optional[float] = None,
                   bearing_deg: float = 0.0,
                   pt_w: Optional[float] = DEFAULT_PT_W,
                   gain_db: Optional[float] = DEFAULT_GAIN_DB,
                   freq_hz: Optional[float] = DEFAULT_FREQ_HZ,
                   pmin_w: Optional[float] = DEFAULT_PMIN_W,
                   loss: float = DEFAULT_LOSS,
                   explicit_range_m: Optional[float] = None) -> BeaconRecord:
        """TA action manageEwBeacon (spawn).

        [a] L2SYS-REQ-03.02: "spawn and classify (FRIEND, FOE, UNKNOWN)
            radar emitters with defined maximum ranges."
        [a: D2] event-driven: only this beacon is transmitted.
        """
        classification = str(classification).upper()
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {CLASSIFICATIONS}")
        if lat_deg is None or lon_deg is None:
            # placed relative to the ownship, as the reference GUI's
            # "Initial Range" / "Initial Theta" fields do [b]
            lat_deg, lon_deg = offset_position(
                self.ownship.lat_deg, self.ownship.lon_deg,
                float(range_m if range_m is not None else 5000.0),
                bearing_deg)
        rec = BeaconRecord(beacon_id=self._next_id,
                           classification=classification,
                           lat_deg=float(lat_deg), lon_deg=float(lon_deg),
                           alt_m=float(alt_m), pt_w=pt_w, gain_db=gain_db,
                           freq_hz=freq_hz, pmin_w=pmin_w, loss=loss,
                           explicit_range_m=explicit_range_m)
        self._next_id += 1
        self.beacons[rec.beacon_id] = rec
        self.evaluate(rec)
        self.transmit(rec)                       # [a: D2] only the change
        return rec

    def update_beacon(self, beacon_id: int, **params) -> BeaconRecord:
        """TA action manageEwBeacon (edit).

        [a] L2SYS-REQ-03.03: "set and adjust, for each beacon, the emitter
            parameters of the radar equation (transmit power, antenna gain,
            frequency, receiver sensitivity)."
        [a: D2] an edit retransmits only the changed beacon.
        """
        rec = self.beacons[int(beacon_id)]
        for key, value in params.items():
            if not hasattr(rec, key):
                raise AttributeError(f"unknown beacon parameter {key!r}")
            setattr(rec, key, value)
        self.evaluate(rec)
        self.transmit(rec)
        return rec

    def remove_beacon(self, beacon_id: int) -> RadarBeacon:
        """TA action manageEwBeacon (remove).

        [a] L2SYS-REQ-03.02 (the GUI removes beacons dynamically; see also
            req_scenarioManagement in fsim_Requirements.sysml).
        [a: D2] "deletion via tombstone (active=False)".
        """
        rec = self.beacons.pop(int(beacon_id))
        tomb = rec.to_message(self.ownship_rcs_m2).tombstone()
        self._send(tomb)
        return tomb

    # -- controlOwnshipRcs -----------------------------------------------
    def set_ownship_rcs(self, rcs_m2: float) -> None:
        """TA action controlOwnshipRcs.

        [a] L2SYS-REQ-03.04: "set and adjust the ownship radar cross
            section as a scenario parameter."
        [a: D12] "an RCS change recomputes and retransmits every beacon."
        """
        self.ownship_rcs_m2 = float(rcs_m2)
        self.evaluate_all()
        for rec in self.beacons.values():
            self.transmit(rec)

    # -- calcBeaconOwnshipRange + calcRadarEquation ----------------------
    def evaluate(self, rec: BeaconRecord) -> BeaconRecord:
        """[a] L2SYS-REQ-03.05: slant range + radar equation -> detection."""
        rec.slant_range_m = slant_range_m(self.ownship, rec.lat_deg,
                                          rec.lon_deg, rec.alt_m)
        # [a: D12] "detection = slant <= R_max"
        rec.detected = rec.slant_range_m <= rec.max_range_m(self.ownship_rcs_m2)
        return rec

    def evaluate_all(self) -> None:
        for rec in self.beacons.values():
            self.evaluate(rec)

    # -- transmission ----------------------------------------------------
    def _send(self, msg: RadarBeacon) -> None:
        self.transmitted.append(msg)
        try:
            self._tx.sendto(msg.to_json(), (self.host, self.tx_port))
        except OSError:                          # [a: D3] teardown race
            pass

    def transmit(self, rec: BeaconRecord) -> RadarBeacon:
        """[a: D2] "transmit only the changed beacon"."""
        msg = rec.to_message(self.ownship_rcs_m2)
        self._send(msg)
        return msg

    def refresh_all(self) -> int:
        """[a: D2] "background refresh every 2.0 s" of the whole set, so a
        receiver that joined late converges and live beacons never age out
        of its 5.0 s stale window."""
        for rec in self.beacons.values():
            self.transmit(rec)
        return len(self.beacons)

    def close(self) -> None:
        """[a: D3] OSError-tolerant teardown."""
        for sock in (self._rx, self._tx):
            if sock is None:
                continue
            try:
                sock.close()
            except OSError:
                pass
        self._rx = None
        self.connected = False


# ==========================================================================
# Qt GUI  (part threadMainGui)
# ==========================================================================

#: [a: D14] dark QSS, shared look with the PCS.
SEM_QSS = """
QWidget { background-color: #1b1b1b; color: #d0d0d0;
          font-family: Consolas, 'DejaVu Sans Mono', monospace; }
QGroupBox { border: 1px solid #444; margin-top: 8px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #9fd39f; }
QPushButton { background-color: #2e2e2e; border: 1px solid #555;
              padding: 5px; }
QPushButton:hover { background-color: #3a3a3a; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QListWidget {
    background-color: #101010; border: 1px solid #444; }
"""


def build_window(core: Optional[SemCore] = None):
    """Construct the SEM window (part threadMainGui of the TA).

    Imported lazily so the headless SemCore can be used without Qt.
    """
    from PyQt5 import QtCore, QtWidgets

    class SemWindow(QtWidgets.QWidget):
        """SEM (Synthetic Environment Manager) window.

        Layout follows the reference screenshot Desired_UIUX_capture.JPG:
        UDP Federation Status / Define Emitter Beacon / Active Emitter
        Roster (Live Pr Calc).
        """

        def __init__(self, core: SemCore):
            super().__init__()
            self.core = core
            self.setWindowTitle("SEM (Synthetic Environment Manager)")
            self.setStyleSheet(SEM_QSS)
            root = QtWidgets.QVBoxLayout(self)

            # -- UDP Federation Status (connectToSim) --------------------
            status_box = QtWidgets.QGroupBox("UDP Federation Status")
            sl = QtWidgets.QHBoxLayout(status_box)
            self.connect_button = QtWidgets.QPushButton("CONNECT TO SIM")
            self.connect_button.clicked.connect(self.on_connect)
            self.status_label = QtWidgets.QLabel("Status: Disconnected")
            self.status_label.setStyleSheet("color:#e05050;")
            sl.addWidget(self.connect_button)
            sl.addWidget(self.status_label)
            root.addWidget(status_box)

            # -- Define Emitter Beacon (manageEwBeacon) ------------------
            def_box = QtWidgets.QGroupBox("Define Emitter Beacon")
            grid = QtWidgets.QGridLayout(def_box)

            self.type_combo = QtWidgets.QComboBox()
            # [a] 03.02 classify FRIEND / FOE / UNKNOWN
            self.type_combo.addItems(list(CLASSIFICATIONS))
            self.range_spin = QtWidgets.QDoubleSpinBox()
            self.range_spin.setRange(0.0, 1.0e6)
            self.range_spin.setValue(5000.0)
            self.range_spin.setSuffix(" m")
            self.theta_spin = QtWidgets.QDoubleSpinBox()
            self.theta_spin.setRange(0.0, 360.0)
            self.theta_spin.setValue(45.0)
            self.theta_spin.setSuffix(" deg")

            # [a] 03.03 per-beacon emitter parameters
            self.pt_spin = QtWidgets.QDoubleSpinBox()
            self.pt_spin.setRange(0.0, 1.0e9)
            self.pt_spin.setDecimals(1)
            self.pt_spin.setValue(DEFAULT_PT_W)
            self.pt_spin.setSuffix(" W")
            self.gain_spin = QtWidgets.QDoubleSpinBox()
            self.gain_spin.setRange(-20.0, 80.0)
            self.gain_spin.setValue(DEFAULT_GAIN_DB)
            self.gain_spin.setSuffix(" dB")
            self.freq_spin = QtWidgets.QDoubleSpinBox()
            self.freq_spin.setRange(0.001, 1000.0)
            self.freq_spin.setDecimals(3)
            self.freq_spin.setValue(DEFAULT_FREQ_HZ / 1e9)
            self.freq_spin.setSuffix(" GHz")
            self.freq_spin.valueChanged.connect(self._update_wavelength)
            self.lambda_label = QtWidgets.QLabel()
            self.pmin_spin = QtWidgets.QDoubleSpinBox()
            self.pmin_spin.setRange(-200.0, 0.0)
            self.pmin_spin.setValue(-90.0)
            self.pmin_spin.setSuffix(" dBm")
            self.loss_spin = QtWidgets.QDoubleSpinBox()
            self.loss_spin.setRange(1.0, 100.0)
            self.loss_spin.setValue(DEFAULT_LOSS)

            # [a] 03.04 ownship RCS as a SCENARIO parameter [a: D12]
            self.rcs_spin = QtWidgets.QDoubleSpinBox()
            self.rcs_spin.setRange(0.0001, 10000.0)
            self.rcs_spin.setDecimals(4)
            self.rcs_spin.setValue(DEFAULT_OWNSHIP_RCS_M2)
            self.rcs_spin.setSuffix(" m2")
            self.rcs_spin.valueChanged.connect(self.on_rcs_changed)

            self.rmax_label = QtWidgets.QLabel()

            rows = [("Type:", self.type_combo),
                    ("Initial Range:", self.range_spin),
                    ("Initial Theta:", self.theta_spin),
                    ("Pt (Power):", self.pt_spin),
                    ("G (Gain):", self.gain_spin),
                    ("Frequency (f):", self.freq_spin),
                    ("Wavelength (lambda):", self.lambda_label),
                    ("Pmin:", self.pmin_spin),
                    ("Loss (L):", self.loss_spin),
                    ("Ownship RCS (sigma):", self.rcs_spin),
                    ("Computed R_max:", self.rmax_label)]
            for r, (label, widget) in enumerate(rows):
                grid.addWidget(QtWidgets.QLabel(label), r, 0)
                grid.addWidget(widget, r, 1)

            self.add_button = QtWidgets.QPushButton("ADD BEACON TO GROUND")
            self.add_button.clicked.connect(self.on_add_beacon)
            grid.addWidget(self.add_button, len(rows), 0, 1, 2)
            root.addWidget(def_box)

            # -- Active Emitter Roster (updateSEUI) ----------------------
            roster_box = QtWidgets.QGroupBox("Active Emitter Roster "
                                             "(Live Pr Calc)")
            rl = QtWidgets.QVBoxLayout(roster_box)
            self.roster = QtWidgets.QListWidget()
            rl.addWidget(self.roster)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Index:"))
            self.index_spin = QtWidgets.QSpinBox()
            self.index_spin.setRange(1, 9999)
            row.addWidget(self.index_spin)
            self.delete_button = QtWidgets.QPushButton("DELETE BEACON")
            self.delete_button.clicked.connect(self.on_delete_beacon)
            row.addWidget(self.delete_button)
            rl.addLayout(row)
            root.addWidget(roster_box)

            # -- timers --------------------------------------------------
            # [a: D2] background refresh every 2.0 s
            self.refresh_timer = QtCore.QTimer(self)
            self.refresh_timer.setInterval(int(BEACON_REFRESH_S * 1000))
            self.refresh_timer.timeout.connect(self.core.refresh_all)
            # ownship polling + roster update (updateSEUI)
            self.poll_timer = QtCore.QTimer(self)
            self.poll_timer.setInterval(50)
            self.poll_timer.timeout.connect(self.on_poll)

            self._update_wavelength()
            self._refresh_roster()

        # -- slots -------------------------------------------------------
        def on_connect(self) -> None:
            """TA action connectToSim."""
            self.core.connect_to_sim()
            self.status_label.setText("Status: Connected")
            self.status_label.setStyleSheet("color:#50e050;")
            self.refresh_timer.start()
            self.poll_timer.start()

        def _emitter_kwargs(self) -> dict:
            return dict(pt_w=self.pt_spin.value(),
                        gain_db=self.gain_spin.value(),
                        freq_hz=self.freq_spin.value() * 1e9,
                        # dBm -> W [b]
                        pmin_w=10.0 ** ((self.pmin_spin.value() - 30.0) / 10.0),
                        loss=self.loss_spin.value())

        def _update_wavelength(self) -> None:
            lam = wavelength_m(self.freq_spin.value() * 1e9)
            self.lambda_label.setText(f"{lam:.4f} m")
            self._update_rmax()

        def _update_rmax(self) -> None:
            """[a: R-7] "live computed R_max"."""
            k = self._emitter_kwargs()
            r = calc_radar_equation(k["pt_w"], k["gain_db"], k["freq_hz"],
                                    self.rcs_spin.value(), k["pmin_w"],
                                    k["loss"])
            self.rmax_label.setText(f"{r / 1000.0:.2f} km")

        def on_add_beacon(self) -> None:
            """TA action manageEwBeacon; [a] L2SYS-REQ-03.02."""
            self.core.add_beacon(
                classification=self.type_combo.currentText(),
                range_m=self.range_spin.value(),
                bearing_deg=self.theta_spin.value(),
                **self._emitter_kwargs())
            self._refresh_roster()

        def on_delete_beacon(self) -> None:
            """[a: D2] tombstone deletion from the GUI."""
            bid = self.index_spin.value()
            if bid in self.core.beacons:
                self.core.remove_beacon(bid)
                self._refresh_roster()

        def on_rcs_changed(self, value: float) -> None:
            """TA action controlOwnshipRcs; [a] L2SYS-REQ-03.04, [a: D12]."""
            self.core.set_ownship_rcs(float(value))
            self._update_rmax()
            self._refresh_roster()

        def on_poll(self) -> None:
            if self.core.poll_ownship() is not None:
                self._refresh_roster()

        def _refresh_roster(self) -> None:
            """TA action updateSEUI."""
            self.roster.clear()
            for rec in self.core.beacons.values():
                rmax = rec.max_range_m(self.core.ownship_rcs_m2)
                self.roster.addItem(
                    f"[{rec.beacon_id}] {rec.classification:<7} "
                    f"Rmax={rmax / 1000.0:7.2f} km  "
                    f"slant={rec.slant_range_m / 1000.0:7.2f} km  "
                    f"{'DETECTED' if rec.detected else '--'}")

        def closeEvent(self, event):                # noqa: N802 (Qt name)
            self.refresh_timer.stop()
            self.poll_timer.stop()
            self.core.close()
            super().closeEvent(event)

    return SemWindow(core if core is not None else SemCore())


def main(argv=None) -> int:
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication(sys.argv if argv is None else list(argv))
    win = build_window()
    win.resize(520, 760)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
