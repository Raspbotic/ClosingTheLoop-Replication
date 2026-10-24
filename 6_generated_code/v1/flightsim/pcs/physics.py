"""
flightsim/pcs/physics.py
========================
Flight Dynamics Model engine interface: the PCS physics thread.

TRACEABILITY
------------
[a] Implements part def ThreadPhysics (fsim_TA_PCS_ThreadPhysics.sysml),
    whose doc comment names this file: "FDM Engine Interface (physics.py)".
    Structure follows the TA action groups exactly:
        initializationPhase  -> _init_fdm / _set_initial_conditions /
                                _update_environment  (aperiodic)
        cyclicExecutionLoop  -> step()               (periodic, 120 Hz)
            acquireControlInputs   -> _acquire_control_inputs()
            mapInputsToFDM         -> _map_inputs_to_fdm()
            executePhysicsStep     -> self._fdm.run()
            extractTelemetry       -> telemetry()
            trasmitTelemetrySignal -> self._telemetry_sink(state)
                                      (port i_OwnTelemetryOut)
            trasmitVisualTelemetry -> _transmit_visual_telemetry()
                                      (port e_nativeJSBSimFdmOut, 5550)
[b] I-3 of docs/ARCHITECTURE.md: fsim_LA.sysml declares a VirtualOwnship
    logical block, but no TA file instantiates it; ThreadPhysics is the
    only TA element owning the FDM, so the requirements the R2F matrix
    allocates to VirtualOwnship (01.02, 01.02b, 01.06b-e, 01.07) are
    implemented here.

REQUIREMENTS IMPLEMENTED HERE
-----------------------------
[a] L2SYS-REQ-01.02  - the system shall comprise a Virtual Ownship.
[a] L2SYS-REQ-01.02b - physical state at 120 Hz from received controls.
[a] L2SYS-REQ-01.04  - initialize on ground at a user-selected airport.
[a] L2SYS-REQ-01.06b - engine start/stop function.
[a] L2SYS-REQ-01.06c - park brake function.
[a] L2SYS-REQ-01.06d - air brake function.
[a] L2SYS-REQ-01.06e - landing gear function.
[a] L2SYS-REQ-01.07  - engine initializes OFF with zero fuel consumption;
                       on start at idle it spools and fuel burn commences.

DECISIONS IMPLEMENTED HERE
--------------------------
[a: D6]  JSBSim model "f16"; the FGNetFDM stream feeds the YF-16 visual.
[a: D4]  FGNetFDM version 24, 408 bytes, big-endian, decimated to ~60 Hz
         on 127.0.0.1:5550.
[a: D7]  btn2 = landing-gear toggle, btn4 = air-brake toggle,
         btn6 = momentary wheel brake; yaw drives BOTH fcs/rudder-cmd-norm
         and fcs/steer-cmd-norm (nose-wheel steering).
[a: D8]  spawn on runway with engine OFF (propulsion/cutoff_cmd -- the
         underscore spelling; the hyphenated name fails silently),
         PRK BRK ON, gear down; engine start refused above 10% throttle
         (idle gate); start = cutoff 0 + set-running=-1; set-position
         airborne implies engine ON and parking brake released; reset =
         clean slate (fuel-freeze disengaged) publishing one telemetry and
         one FG frame even while flight-frozen.
[a: D9]  terrain elevation fed into position/terrain-elevation-asl-ft,
         slewed at most 1 ft per 120 Hz frame.
[a: D15] the cyclic loop is stoppable via stop_event.
"""

from __future__ import annotations

import copy
import math
import socket
import struct
import threading
import time
from typing import Callable, Dict, Optional

from flightsim.common import ports
from flightsim.common.messages import HotasControls, OwnshipState
from flightsim.pcs.airports import Airport, AirportTable, DEFAULT_TABLE

# --------------------------------------------------------------------------
# Unit conversions [b]
# --------------------------------------------------------------------------
FT_PER_M = 1.0 / 0.3048
M_PER_FT = 0.3048
LB_PER_KG = 1.0 / 0.45359237
KG_PER_LB = 0.45359237
KT_PER_FPS = 0.592484

# --------------------------------------------------------------------------
# D7 button map
# --------------------------------------------------------------------------
#: [a: D7] "btn2 = landing-gear toggle; btn4 = air-brake toggle;
#:          btn6 = momentary wheel brake"
BTN_GEAR_TOGGLE = 2
BTN_AIRBRAKE_TOGGLE = 4
BTN_WHEEL_BRAKE = 6

# --------------------------------------------------------------------------
# D8 idle gate
# --------------------------------------------------------------------------
#: [a: D8] "engine start refused above 10% throttle (idle gate)"
IDLE_GATE_THROTTLE = 0.10

# --------------------------------------------------------------------------
# D9 terrain slew limit
# --------------------------------------------------------------------------
#: [a: D9] "slewed <= 1 ft per 120 Hz frame"
TERRAIN_SLEW_FT_PER_FRAME = 1.0

# --------------------------------------------------------------------------
# FGNetFDM v24 packet  [a: D4]
# --------------------------------------------------------------------------
#: Big-endian layout of FlightGear's FGNetFDM structure, FG_NET_FDM_VERSION
#: 24, with FG_MAX_ENGINES=4, FG_MAX_WHEELS=3, FG_MAX_TANKS=4.
#: [a: D4] "FGNetFDM version 24, 408 bytes, big-endian".
FGNETFDM_FORMAT = ">II3d22fI4I36fI4fI3I9fIif10f"
FGNETFDM_VERSION = 24
FGNETFDM_SIZE = struct.calcsize(FGNETFDM_FORMAT)  # == 408, asserted in tests


def pack_fgnetfdm(state: Dict[str, float]) -> bytes:
    """Serialize one visual-telemetry frame for FlightGear. [a: D4]

    The caller supplies SI/JSBSim values; the field order below is the
    FGNetFDM v24 declaration order.
    """
    values = []
    values.append(FGNETFDM_VERSION)                       # version
    values.append(0)                                      # padding
    values.append(math.radians(state["lon_deg"]))         # longitude, rad
    values.append(math.radians(state["lat_deg"]))         # latitude, rad
    values.append(state["alt_m"])                         # altitude, m ASL
    values.append(state["agl_m"])                         # agl, m
    values.append(math.radians(state["roll_deg"]))        # phi
    values.append(math.radians(state["pitch_deg"]))       # theta
    values.append(math.radians(state["heading_deg"]))     # psi
    values.append(state.get("alpha_rad", 0.0))
    values.append(state.get("beta_rad", 0.0))
    values.append(state.get("phidot", 0.0))
    values.append(state.get("thetadot", 0.0))
    values.append(state.get("psidot", 0.0))
    values.append(state.get("vcas_kt", 0.0))
    values.append(state.get("climb_rate_fps", 0.0))
    values.append(state.get("v_north_fps", 0.0))
    values.append(state.get("v_east_fps", 0.0))
    values.append(state.get("v_down_fps", 0.0))
    values.extend([0.0, 0.0, 0.0])                        # v_body u/v/w
    values.extend([0.0, 0.0, 0.0])                        # A_X/Y/Z pilot
    values.append(0.0)                                    # stall_warning
    values.append(0.0)                                    # slip_deg
    values.append(1)                                      # num_engines
    values.extend([1 if state.get("engine_running") else 0, 0, 0, 0])
    rpm = state.get("rpm_pct", 0.0)
    values.extend([rpm, 0.0, 0.0, 0.0])                   # rpm[4]
    for _ in range(8):                                    # fuel_flow, fuel_px,
        values.extend([0.0, 0.0, 0.0, 0.0])               # egt, cht, mp_osi,
    values.append(1)                                      # tit, oil_temp,
    #                                                       oil_px -> num_tanks
    values.extend([state.get("fuel_kg", 0.0) * LB_PER_KG, 0.0, 0.0, 0.0])
    values.append(3)                                      # num_wheels
    wow = 1 if state.get("on_ground") else 0
    values.extend([wow, wow, wow])                        # wow[3]
    gear = state.get("gear_pos", 1.0)
    values.extend([gear, gear, gear])                     # gear_pos[3]
    values.extend([state.get("nose_wheel", 0.0), 0.0, 0.0])  # gear_steer[3]
    values.extend([0.0, 0.0, 0.0])                        # gear_compression[3]
    values.append(int(state.get("cur_time", time.time())) & 0xFFFFFFFF)
    values.append(0)                                      # warp
    values.append(state.get("visibility_m", 32000.0))
    values.append(state.get("elevator", 0.0))
    values.append(0.0)                                    # elevator_trim_tab
    values.extend([0.0, 0.0])                             # left/right flap
    values.append(state.get("left_aileron", 0.0))
    values.append(state.get("right_aileron", 0.0))
    values.append(state.get("rudder", 0.0))
    values.append(state.get("nose_wheel", 0.0))
    values.append(state.get("speedbrake", 0.0))
    values.append(0.0)                                    # spoilers
    return struct.pack(FGNETFDM_FORMAT, *values)


def unpack_fgnetfdm_header(raw: bytes):
    """[b] Test/diagnostic helper: (version, lon_rad, lat_rad, alt_m)."""
    if len(raw) != FGNETFDM_SIZE:
        raise ValueError(f"FGNetFDM frame must be {FGNETFDM_SIZE} bytes, "
                         f"got {len(raw)}")
    version, _pad, lon, lat, alt = struct.unpack_from(">II3d", raw, 0)
    return version, lon, lat, alt


# ==========================================================================
# ThreadPhysics
# ==========================================================================

class ThreadPhysics(threading.Thread):
    """part def ThreadPhysics (fsim_TA_PCS_ThreadPhysics.sysml).

    An aperiodic initialization phase followed by a 120 Hz cyclic
    execution loop [a: L2SYS-REQ-01.02b].
    """

    def __init__(self,
                 icao: Optional[str] = None,
                 table: Optional[AirportTable] = None,
                 rate_hz: float = ports.PHYSICS_RATE_HZ,
                 fdm_host: str = ports.LOCALHOST,
                 fdm_port: int = ports.UDP_FG_NATIVE_FDM,
                 send_to_flightgear: bool = True,
                 telemetry_sink: Optional[Callable[[OwnshipState], None]] = None,
                 stop_event: Optional[threading.Event] = None,
                 jsbsim_root: Optional[str] = None,
                 aircraft: str = "f16"):
        super().__init__(daemon=True, name="physics-thread")
        self.table = table or DEFAULT_TABLE
        self.airport: Airport = self.table.get(icao) if icao \
            else self.table.default()
        self.rate_hz = float(rate_hz)
        self.dt = 1.0 / self.rate_hz
        self.aircraft = aircraft          # [a: D6] JSBSim model "f16"
        self._jsbsim_root = jsbsim_root
        self._fdm = None

        # -- inter-thread ports ------------------------------------------
        self._lock = threading.RLock()
        self._controls = HotasControls()          # i_HotasControlIn
        self._telemetry_sink = telemetry_sink     # i_OwnTelemetryOut
        self.stop_event = stop_event or threading.Event()  # [a: D15]

        # -- e_nativeJSBSimFdmOut ----------------------------------------
        self._send_fg = send_to_flightgear
        self._fdm_addr = (fdm_host, int(fdm_port))
        self._fg_sock: Optional[socket.socket] = None
        # [a: D4] "decimated to ~60 Hz": one frame every N physics frames.
        self._fg_decimation = max(
            1, int(round(self.rate_hz / ports.FG_FDM_RATE_HZ)))
        self._frame = 0
        self.fg_frames_sent = 0

        # -- system state (01.06b-e, D8) ---------------------------------
        self.engine_on = False            # 01.06b
        self.park_brake = True            # 01.06c, D8 ground start
        self.air_brake = False            # 01.06d
        self.gear_down = True             # 01.06e, D8 ground start
        self.wheel_brake = False          # D7 btn6, momentary
        self.fuel_freeze = False          # ssfFreezeFuelConsupmtion
        self.paused = True                # flight freeze; RUN SIM releases it
        self._frozen_fuel_lbs: Optional[list] = None
        self._prev_switches: Dict[str, bool] = {}
        self._terrain_target_ft: Optional[float] = None
        self.messages: list = []          # surfaced in the PCS message window

        self._init_fdm()
        self._set_initial_conditions()
        self._update_environment()

    # ------------------------------------------------------------------
    # initializationPhase (aperiodic) [a: TA action group 1]
    # ------------------------------------------------------------------
    def _init_fdm(self) -> None:
        """TA action initFDM. [a: D6] JSBSim model 'f16'."""
        import jsbsim
        self._fdm = jsbsim.FGFDMExec(self._jsbsim_root)
        self._fdm.set_debug_level(0)
        if not self._fdm.load_model(self.aircraft):
            raise RuntimeError(f"JSBSim could not load model {self.aircraft!r}")
        self._fdm.set_dt(self.dt)         # [a] 01.02b: 120 Hz frame time
        # [b] JSBSim exposes no "number of tanks" property; query the
        # propulsion object once and cache it.
        try:
            self._n_tanks = int(self._fdm.get_propulsion().get_num_tanks())
        except Exception:
            self._n_tanks = 0
        if self._n_tanks <= 0:
            self._n_tanks = 0
            while True:
                try:
                    self._fdm[f"propulsion/tank[{self._n_tanks}]/contents-lbs"]
                except KeyError:
                    break
                self._n_tanks += 1

    def _set_initial_conditions(self) -> None:
        """TA action setInitialConditions.

        [a] L2SYS-REQ-01.04: initialize on ground at the selected airport.
        [a: D8] spawn on runway with engine OFF, PRK BRK ON, gear down.
        """
        ap = self.airport
        f = self._fdm
        f["ic/lat-gc-deg"] = ap.lat_deg
        f["ic/long-gc-deg"] = ap.lon_deg
        f["ic/terrain-elevation-ft"] = ap.elevation_m * FT_PER_M
        f["ic/h-agl-ft"] = 4.0            # [c] gear-height offset on the runway
        f["ic/psi-true-deg"] = ap.runway_heading_deg
        f["ic/vc-kts"] = 0.0
        f["ic/gamma-deg"] = 0.0
        f.run_ic()

        # [a: D8] engine OFF. NOTE the underscore: propulsion/cutoff_cmd.
        # The hyphenated spelling 'propulsion/cutoff-cmd' is silently
        # accepted by JSBSim as a NEW property and has no effect.
        f["propulsion/cutoff_cmd"] = 1.0
        f["fcs/throttle-cmd-norm"] = 0.0
        self.engine_on = False
        # [a: D8] PRK BRK ON, gear down.
        self.park_brake = True
        self.gear_down = True
        self.air_brake = False
        self.wheel_brake = False
        f["gear/gear-cmd-norm"] = 1.0
        f["fcs/speedbrake-cmd-norm"] = 0.0
        self._apply_brakes()
        self._frame = 0
        self._terrain_target_ft = ap.elevation_m * FT_PER_M

    def _update_environment(self) -> None:
        """TA action updateEnvironment.

        [a: D9] seeds position/terrain-elevation-asl-ft from the airport
        elevation until FlightGear starts publishing ground elevation.
        """
        self._fdm["position/terrain-elevation-asl-ft"] = \
            self.airport.elevation_m * FT_PER_M

    # ------------------------------------------------------------------
    # Control inputs (port i_HotasControlIn)
    # ------------------------------------------------------------------
    def set_controls(self, controls: HotasControls) -> None:
        """Port i_HotasControlIn: accept the current pilot commands.

        [a] fsim_TA_PCS.sysml binding threadFlightSim.i_HotasControlOut ->
            threadPhysics.i_HotasControlIn.
        """
        with self._lock:
            self._controls = controls
            self._apply_switch_edges(controls.switches)

    def _apply_switch_edges(self, switches: Dict[str, bool]) -> None:
        """[a: D7] btn2 = gear toggle, btn4 = air brake toggle,
        btn6 = momentary wheel brake."""
        def pressed(idx: int) -> bool:
            name = f"btn{idx}"
            now = bool(switches.get(name, False))
            was = bool(self._prev_switches.get(name, False))
            return now and not was

        if pressed(BTN_GEAR_TOGGLE):
            self.set_gear_down(not self.gear_down)
        if pressed(BTN_AIRBRAKE_TOGGLE):
            self.set_air_brake(not self.air_brake)
        # momentary: follows the button state rather than its edge [a: D7]
        self.wheel_brake = bool(switches.get(f"btn{BTN_WHEEL_BRAKE}", False))
        self._prev_switches = dict(switches)

    # ------------------------------------------------------------------
    # System controls  [a] 01.06b-e
    # ------------------------------------------------------------------
    def set_engine(self, on: bool) -> bool:
        """L2SYS-REQ-01.06b engine start/stop; L2SYS-REQ-01.07 start
        semantics; [a: D8] idle gate and start sequence.

        Returns False when the start is refused by the idle gate.
        """
        with self._lock:
            if on:
                if self._controls.throttle > IDLE_GATE_THROTTLE:
                    self.messages.append(
                        "ENGINE START REFUSED - THROTTLE ABOVE IDLE")
                    return False                     # [a: D8] idle gate
                # [a: D8] start = cutoff 0 + set-running = -1
                self._fdm["propulsion/cutoff_cmd"] = 0.0
                self._fdm["propulsion/set-running"] = -1
                self.engine_on = True
                self.messages.append("ENGINE: ON")
            else:
                self._fdm["propulsion/cutoff_cmd"] = 1.0
                self.engine_on = False
                self.messages.append("ENGINE: OFF")
            return True

    def set_park_brake(self, on: bool) -> None:
        """L2SYS-REQ-01.06c park brake (ON/OFF)."""
        with self._lock:
            self.park_brake = bool(on)
            self._apply_brakes()

    def set_air_brake(self, on: bool) -> None:
        """L2SYS-REQ-01.06d air brake (ON/OFF)."""
        with self._lock:
            self.air_brake = bool(on)
            self._fdm["fcs/speedbrake-cmd-norm"] = 1.0 if on else 0.0

    def set_gear_down(self, down: bool) -> None:
        """L2SYS-REQ-01.06e landing gear (UP/DOWN)."""
        with self._lock:
            self.gear_down = bool(down)
            self._fdm["gear/gear-cmd-norm"] = 1.0 if down else 0.0

    def _apply_brakes(self) -> None:
        """[b] Park brake and the momentary wheel brake both act on the
        JSBSim wheel-brake commands; the park brake latches."""
        cmd = 1.0 if (self.park_brake or self.wheel_brake) else 0.0
        self._fdm["fcs/left-brake-cmd-norm"] = cmd
        self._fdm["fcs/right-brake-cmd-norm"] = cmd

    # ------------------------------------------------------------------
    # D9 terrain feedback
    # ------------------------------------------------------------------
    def set_terrain_elevation_m(self, elevation_m: float) -> None:
        """Port from BackgroundIOManager.terrainElevationManager.

        [a: D9] "Poll FG ground elevation at 2 Hz; feed into JSBSim
        position/terrain-elevation-asl-ft slewed <= 1 ft per 120 Hz frame."
        The value is stored as a TARGET; the cyclic loop slews towards it.
        """
        with self._lock:
            self._terrain_target_ft = float(elevation_m) * FT_PER_M

    def _slew_terrain(self) -> None:
        """[a: D9] at most 1 ft of terrain movement per frame."""
        target = self._terrain_target_ft
        if target is None:
            return
        current = self._fdm["position/terrain-elevation-asl-ft"]
        delta = target - current
        if abs(delta) <= TERRAIN_SLEW_FT_PER_FRAME:
            self._fdm["position/terrain-elevation-asl-ft"] = target
        else:
            self._fdm["position/terrain-elevation-asl-ft"] = (
                current + math.copysign(TERRAIN_SLEW_FT_PER_FRAME, delta))

    # ------------------------------------------------------------------
    # cyclicExecutionLoop (periodic, 120 Hz) [a: TA action group 2]
    # ------------------------------------------------------------------
    def _acquire_control_inputs(self) -> HotasControls:
        """TA action acquireControlInputs."""
        with self._lock:
            return self._controls

    def _map_inputs_to_fdm(self, c: HotasControls) -> None:
        """TA action mapInputsToFDM.

        [a: D7] "yaw drives BOTH fcs/rudder-cmd-norm and fcs/steer-cmd-norm
        (nose-wheel steering)."
        [b] roll -> aileron: JSBSim's fcs/aileron-cmd-norm is positive for
        a right roll, which matches the stick convention directly
        (measured on the f16 model: +0.3 -> +45 deg of bank).
        [b] pitch -> elevator, NEGATED. D10 fixes the pilot-side sign as
        "drag-down = stick back = pitch +1", i.e. pitch +1 must command
        NOSE UP; JSBSim's fcs/elevator-cmd-norm is positive NOSE DOWN
        (measured on the f16 model: +0.3 -> theta -19 deg, -0.3 -> theta
        +12 deg). The negation reconciles the two conventions and is the
        single place where the pilot sign convention is applied.
        [b] throttle -> fcs/throttle-cmd-norm directly.
        """
        f = self._fdm
        f["fcs/aileron-cmd-norm"] = c.roll
        f["fcs/elevator-cmd-norm"] = -c.pitch
        f["fcs/rudder-cmd-norm"] = c.yaw          # [a: D7]
        f["fcs/steer-cmd-norm"] = c.yaw           # [a: D7] nose-wheel steering
        f["fcs/throttle-cmd-norm"] = c.throttle
        self._apply_brakes()

    def _hold_fuel(self) -> None:
        """TA action ssfFreezeFuelConsupmtion backend: "hold tank contents
        constant" [a] L2SYS-REQ-01.10b FUEL FREEZE."""
        if not self.fuel_freeze:
            self._frozen_fuel_lbs = None
            return
        n = self._n_tanks
        if self._frozen_fuel_lbs is None:
            self._frozen_fuel_lbs = [
                self._fdm[f"propulsion/tank[{i}]/contents-lbs"]
                for i in range(n)]
        for i, lbs in enumerate(self._frozen_fuel_lbs):
            self._fdm[f"propulsion/tank[{i}]/contents-lbs"] = lbs

    def step(self) -> OwnshipState:
        """One cyclic frame: the full TA action sequence of group 2.

        [a] L2SYS-REQ-01.02b: one call advances the FDM by exactly
            1/120 s of simulated time.
        """
        controls = self._acquire_control_inputs()   # acquireControlInputs
        with self._lock:
            self._map_inputs_to_fdm(controls)       # mapInputsToFDM
            self._slew_terrain()                    # [a: D9]
            self._fdm.run()                         # executePhysicsStep
            self._hold_fuel()
            state = self.telemetry()                # extractTelemetry
            self._frame += 1
            if self._telemetry_sink is not None:    # trasmitTelemetrySignal
                self._telemetry_sink(state)         # port i_OwnTelemetryOut
            if self._frame % self._fg_decimation == 0:
                self._transmit_visual_telemetry(state)  # trasmitVisual...
        return state

    def telemetry(self, seq: Optional[int] = None) -> OwnshipState:
        """TA action extractTelemetry -> the D1 OwnshipState message.

        [a] L2SYS-REQ-01.02b names position, attitude and velocity.
        """
        f = self._fdm
        fuel_lbs = sum(f[f"propulsion/tank[{i}]/contents-lbs"]
                       for i in range(self._n_tanks))
        return OwnshipState(
            lat_deg=f["position/lat-gc-deg"],
            lon_deg=f["position/long-gc-deg"],
            alt_m=f["position/h-sl-ft"] * M_PER_FT,
            agl_m=f["position/h-agl-ft"] * M_PER_FT,
            roll_deg=f["attitude/phi-deg"],
            pitch_deg=f["attitude/theta-deg"],
            heading_deg=f["attitude/psi-deg"],
            speed_mps=f["velocities/vc-fps"] * M_PER_FT,
            vspeed_mps=f["velocities/h-dot-fps"] * M_PER_FT,
            pitch_rate_dps=math.degrees(f["velocities/q-rad_sec"]),
            fuel_kg=fuel_lbs * KG_PER_LB,
            rpm_pct=f["propulsion/engine[0]/n2"],
            on_ground=bool(f["gear/unit[0]/WOW"]),
            engine_running=self.engine_on,
            sim_time_s=f["simulation/sim-time-sec"],
            seq=self._frame if seq is None else seq,
        )

    def _transmit_visual_telemetry(self, state: OwnshipState) -> None:
        """TA action trasmitVisualTelemetry -> port e_nativeJSBSimFdmOut.

        [a: D4] 408-byte big-endian FGNetFDM v24 on 5550, ~60 Hz.
        """
        frame = self.build_fg_frame(state)
        self.fg_frames_sent += 1
        if not self._send_fg:
            return
        if self._fg_sock is None:
            self._fg_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._fg_sock.sendto(frame, self._fdm_addr)
        except OSError:                    # [a: D3] tolerate teardown races
            pass

    def build_fg_frame(self, state: OwnshipState) -> bytes:
        """[a: D4] Build (without sending) one FGNetFDM v24 frame."""
        f = self._fdm
        return pack_fgnetfdm({
            "lat_deg": state.lat_deg, "lon_deg": state.lon_deg,
            "alt_m": state.alt_m, "agl_m": state.agl_m,
            "roll_deg": state.roll_deg, "pitch_deg": state.pitch_deg,
            "heading_deg": state.heading_deg,
            "alpha_rad": f["aero/alpha-rad"], "beta_rad": f["aero/beta-rad"],
            "vcas_kt": f["velocities/vc-fps"] * KT_PER_FPS,
            "climb_rate_fps": f["velocities/h-dot-fps"],
            "v_north_fps": f["velocities/v-north-fps"],
            "v_east_fps": f["velocities/v-east-fps"],
            "v_down_fps": f["velocities/v-down-fps"],
            "rpm_pct": state.rpm_pct, "fuel_kg": state.fuel_kg,
            "on_ground": state.on_ground,
            "engine_running": self.engine_on,
            "gear_pos": 1.0 if self.gear_down else 0.0,
            "nose_wheel": f["fcs/steer-cmd-norm"],
            "elevator": f["fcs/elevator-cmd-norm"],
            "left_aileron": f["fcs/aileron-cmd-norm"],
            "right_aileron": -f["fcs/aileron-cmd-norm"],
            "rudder": f["fcs/rudder-cmd-norm"],
            "speedbrake": f["fcs/speedbrake-cmd-norm"],
            "cur_time": time.time(),
        })

    def run(self) -> None:
        """The 120 Hz cyclic loop. [a] L2SYS-REQ-01.02b, [a: D15]."""
        next_t = time.monotonic()
        while not self.stop_event.is_set():
            if not self.paused:
                self.step()
            next_t += self.dt
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()      # [b] never spiral on overrun

    def stop(self) -> None:
        """[a: D15] stoppable from a test teardown; [a: D3] OSError-tolerant."""
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout=3.0)
        if self._fg_sock is not None:
            try:
                self._fg_sock.close()
            except OSError:
                pass
            self._fg_sock = None

    # ------------------------------------------------------------------
    # SSF backends  [a] L2SYS-REQ-01.10b
    # ------------------------------------------------------------------
    def ssf_run(self) -> None:
        """RUN SIM: start/resume the simulation loop.
        [a] TA action ssfStartSimulation."""
        self.paused = False

    def ssf_pause(self) -> None:
        """PAUSE SIM: flight freeze. [a] TA action ssfStopSimulation."""
        self.paused = True

    def ssf_reset(self, icao: Optional[str] = None) -> OwnshipState:
        """RESET SIM: reload the initial conditions.

        [a] TA action ssfResetSimulation; L2SYS-REQ-01.10b.
        [a: D8] "reset = clean slate (AP/fuel-freeze disengaged) and
        publishes one telemetry + one FG frame even while flight-frozen."
        (The AP part of the clean slate is applied by the PCS, which owns
        the autopilot; see main_window.PCSMainWindow.ssf_reset.)
        """
        with self._lock:
            if icao:
                self.airport = self.table.get(icao)
            self.fuel_freeze = False              # [a: D8] clean slate
            self._frozen_fuel_lbs = None
            self._controls = HotasControls()
            self._prev_switches = {}
            self._fdm.reset_to_initial_conditions(0)
            self._set_initial_conditions()
            self._update_environment()
            state = self.telemetry()
            # [a: D8] publish one telemetry + one FG frame even while frozen
            if self._telemetry_sink is not None:
                self._telemetry_sink(state)
            self._transmit_visual_telemetry(state)
            return state

    def ssf_set_position(self, lat_deg: float, lon_deg: float, alt_m: float,
                         speed_mps: float = 0.0,
                         heading_deg: Optional[float] = None) -> OwnshipState:
        """SET POSITION: set lat, lon, altitude (and speed/heading).

        [a] TA action ssfSetOwnPosition; L2SYS-REQ-01.10b names
            "Lat, Lon (deg), Altitude (m), Speed (m/s), Heading (deg)".
        [a: D8] "SSF set-position airborne implies engine ON and parking
            brake released."
        """
        with self._lock:
            f = self._fdm
            terrain_ft = f["position/terrain-elevation-asl-ft"]
            alt_ft = float(alt_m) * FT_PER_M
            airborne = (alt_ft - terrain_ft) > 10.0     # [c] 10 ft threshold
            f["ic/lat-gc-deg"] = float(lat_deg)
            f["ic/long-gc-deg"] = float(lon_deg)
            f["ic/h-sl-ft"] = alt_ft
            f["ic/vc-kts"] = float(speed_mps) * FT_PER_M * KT_PER_FPS
            if heading_deg is not None:
                f["ic/psi-true-deg"] = float(heading_deg)
            f["ic/gamma-deg"] = 0.0
            f.run_ic()
            if airborne:                                # [a: D8]
                self.set_park_brake(False)
                f["propulsion/cutoff_cmd"] = 0.0
                f["propulsion/set-running"] = -1
                self.engine_on = True
                self.set_gear_down(False)
                self.messages.append(
                    "SET POSITION (AIRBORNE): ENGINE ON, PRK BRK RELEASED")
            else:
                self._apply_brakes()
            state = self.telemetry()
            if self._telemetry_sink is not None:
                self._telemetry_sink(state)
            self._transmit_visual_telemetry(state)
            return state

    def set_fuel_freeze(self, on: bool) -> None:
        """FUEL FREEZE: hold tank contents constant.
        [a] TA action ssfFreezeFuelConsupmtion; L2SYS-REQ-01.10b.

        [b] The contents are latched at the instant of the command, not at
        the next cyclic frame, so that not even one frame of burn leaks in
        between the button press and the hold taking effect.
        """
        with self._lock:
            self.fuel_freeze = bool(on)
            self._frozen_fuel_lbs = (
                [self._fdm[f"propulsion/tank[{i}]/contents-lbs"]
                 for i in range(self._n_tanks)] if on else None)

    def snapshot_state(self) -> Dict[str, object]:
        """SAVE SNAPSHOT backend: all physics state variables.

        [a] TA action ssfSaveSnapshot ("save all state-variables of the
            simulation, incl: physics, ..."); L2SYS-REQ-01.10b.
        [a] R-8: "snapshot save/load (no aliasing)" - a deep copy is
            returned so a later frame cannot mutate the snapshot.
        """
        with self._lock:
            f = self._fdm
            data = {
                "airport": self.airport.icao,
                "lat_deg": f["position/lat-gc-deg"],
                "lon_deg": f["position/long-gc-deg"],
                "alt_ft": f["position/h-sl-ft"],
                "phi_deg": f["attitude/phi-deg"],
                "theta_deg": f["attitude/theta-deg"],
                "psi_deg": f["attitude/psi-deg"],
                "vc_kts": f["velocities/vc-fps"] * KT_PER_FPS,
                "terrain_ft": f["position/terrain-elevation-asl-ft"],
                "fuel_lbs": [f[f"propulsion/tank[{i}]/contents-lbs"]
                             for i in range(self._n_tanks)],
                "engine_on": self.engine_on,
                "park_brake": self.park_brake,
                "air_brake": self.air_brake,
                "gear_down": self.gear_down,
                "fuel_freeze": self.fuel_freeze,
                "sim_time_s": f["simulation/sim-time-sec"],
            }
            return copy.deepcopy(data)

    def restore_state(self, data: Dict[str, object]) -> OwnshipState:
        """LOAD SNAPSHOT backend. [a] TA action ssfLoadSnapshot."""
        with self._lock:
            data = copy.deepcopy(data)             # [a] R-8 no aliasing
            f = self._fdm
            icao = data.get("airport")
            if icao and icao in self.table:
                self.airport = self.table.get(str(icao))
            f["ic/lat-gc-deg"] = float(data["lat_deg"])
            f["ic/long-gc-deg"] = float(data["lon_deg"])
            f["ic/h-sl-ft"] = float(data["alt_ft"])
            f["ic/phi-deg"] = float(data["phi_deg"])
            f["ic/theta-deg"] = float(data["theta_deg"])
            f["ic/psi-true-deg"] = float(data["psi_deg"])
            f["ic/vc-kts"] = float(data["vc_kts"])
            f["ic/terrain-elevation-ft"] = float(data["terrain_ft"])
            f.run_ic()
            self._terrain_target_ft = float(data["terrain_ft"])
            for i, lbs in enumerate(data["fuel_lbs"]):    # type: ignore[index]
                f[f"propulsion/tank[{i}]/contents-lbs"] = float(lbs)
            self.engine_on = bool(data["engine_on"])
            f["propulsion/cutoff_cmd"] = 0.0 if self.engine_on else 1.0
            if self.engine_on:
                f["propulsion/set-running"] = -1
            self.set_park_brake(bool(data["park_brake"]))
            self.set_air_brake(bool(data["air_brake"]))
            self.set_gear_down(bool(data["gear_down"]))
            self.set_fuel_freeze(bool(data["fuel_freeze"]))
            return self.telemetry()

    def select_airport(self, icao: str) -> None:
        """ssfSelectAirport backend: sets the ground-start initial
        condition. [a] L2SYS-REQ-01.04; freeze gating is enforced by the
        PCS (L2SYS-REQ-01.04b)."""
        with self._lock:
            self.airport = self.table.get(icao)
            self._set_initial_conditions()
            self._update_environment()


__all__ = ["ThreadPhysics", "pack_fgnetfdm", "unpack_fgnetfdm_header",
           "FGNETFDM_FORMAT", "FGNETFDM_SIZE", "FGNETFDM_VERSION",
           "BTN_GEAR_TOGGLE", "BTN_AIRBRAKE_TOGGLE", "BTN_WHEEL_BRAKE",
           "IDLE_GATE_THROTTLE", "TERRAIN_SLEW_FT_PER_FRAME"]
