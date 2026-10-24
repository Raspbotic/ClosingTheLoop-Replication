"""
flightsim.pcs.physics
=====================
ThreadPhysics: the 120 Hz JSBSim flight-dynamics loop of the PCS.

TRACEABILITY
------------
[a] Implements part def ThreadPhysics (fsim_TA_PCS_ThreadPhysics.sysml),
    doc comment names this very file: "physics.py".
[a] Requirement L2SYS-REQ-01.02b (fsim_Design.xlsx): "The VirtualOwnship
    shall compute the aircraft's physical state (position, attitude,
    velocity) at a 120Hz frequency based on received control inputs."
    Allocated logical block: VirtualOwnship. Supports req_manualFlight
    and UC_ManualFlight.
[a] Requirement L2SYS-REQ-01.03 (consumer side): "The VisualSystem shall
    render a 3D external view driven by the native-fdm UDP telemetry
    stream" -- this module is the producer of that stream.
[a] Ports (fsim_TA_PCS_ThreadPhysics.sysml):
      i_HotasControlIn     -> constructor arg 'hotas_in'  (queue.Queue)
      i_OwnTelemetryOut    -> constructor arg 'telemetry_out' (queue.Queue)
      e_nativeJSBSimFdmOut -> UDP socket to 127.0.0.1:5550
                              (ports.UDP_NATIVE_FDM)

TA ACTION -> CODE MAPPING [b]  (TA spelling preserved verbatim, sic)
--------------------------------------------------------------------
  initializationPhase:
    initFDM               -> _init_fdm()
    setInitialConditions  -> _set_initial_conditions()
    updateEnvironment     -> _update_environment()
  cyclicExecutionLoop (120 Hz):
    acquireControlInputs  -> _acquire_control_inputs()
    mapInputsToFDM        -> _map_inputs_to_fdm()
    executePhysicsStep    -> _execute_physics_step()
    extractTelemetry      -> _extract_telemetry()
    trasmitTelemetrySignal-> _transmit_telemetry_signal()   (sic)
    trasmitVisualTelemetry-> _transmit_visual_telemetry()   (sic)

SSF HOOKS [b]
-------------
The UIManager SSF actions (fsim_TA_PCS_ThreadFlightSim_UIM.sysml) that
concern the physics thread are exposed as thread-safe methods:
    ssfStartSimulation        -> ssf_start()
    ssfStopSimulation         -> ssf_stop()
    ssfResetSimulation        -> ssf_reset()
    ssfSetOwnPosition         -> ssf_set_own_position(lat, lon, alt, hdg, kts)
    ssfFreezeFuelConsupmtion  -> ssf_freeze_fuel(bool)      (sic)

DEVELOPER CHOICES [c] (not in the design specification)
--------------------------------------------------------
- Default aircraft "f16" [a: customer decision 2026-07-11, matches the
  YF-16 OTW model]. Configurable; c172x etc. also bundled.
- Default initial conditions: airborne over Milano Linate area at
  1500 m MSL, 120 kts -- arbitrary, replace at will / via SSF.
- Latest-value semantics on the HOTAS queue: the loop drains the queue
  and keeps only the newest datagram (control input is a sampled
  continuous signal; queueing stale samples adds latency, no value).
- Fuel freeze implemented by re-writing tank contents every frame
  (JSBSim exposes no global fuel-freeze property).
- FlightGear FGNetFDM struct version 24 (408 bytes, big-endian). The
  layout is documented FlightGear protocol [a]; the hand-coded packing
  below was written from developer knowledge and is guarded by a size
  assertion (408) -- verify once against your FlightGear version
  (2020.3+) before flight use.
"""

from __future__ import annotations

import math
import queue
import socket
import struct
import threading
import time
from dataclasses import dataclass

import jsbsim

from flightsim.common import ports
from flightsim.common.messages import HotasControls, OwnshipState

FT2M = 0.3048
M2FT = 1.0 / FT2M
KTS2MS = 0.514444

PHYSICS_RATE_HZ = 120.0            # [a] L2SYS-REQ-01.02b + TA "120Hz Loop"
PHYSICS_DT = 1.0 / PHYSICS_RATE_HZ


# ---------------------------------------------------------------------------
# Initial conditions container
# ---------------------------------------------------------------------------

@dataclass
class InitialConditions:
    """Airborne or ground start. [a-pending: L2SYS-REQ-01.04 ground start]"""
    lat_deg: float = 45.45
    lon_deg: float = 9.28
    alt_m: float = 2000.0          # MSL; ignored when on_ground
    heading_deg: float = 90.0
    airspeed_kts: float = 250.0    # [b] F16 cruise entry; 0 when on_ground
    on_ground: bool = False
    terrain_elev_m: float = 0.0    # field elevation (ground start only)


# ---------------------------------------------------------------------------
# FlightGear native-fdm encoder (FGNetFDM v24, 408 bytes, network byte order)
# ---------------------------------------------------------------------------

_FDM_VERSION = 24

# struct format, big-endian; field groups annotated
_FDM_FMT = (
    ">"
    "I I "          # version, padding
    "d d d "        # longitude(rad) latitude(rad) altitude(m)
    "f f f f "      # agl(m) phi theta psi (rad)
    "f f "          # alpha beta
    "f f f "        # phidot thetadot psidot
    "f f "          # vcas(kts) climb_rate(ft/s)
    "f f f "        # v_north v_east v_down (ft/s)
    "f f f "        # v_body_u v_body_v v_body_w
    "f f f "        # A_X_pilot A_Y_pilot A_Z_pilot (ft/s^2)
    "f f "          # stall_warning slip_deg
    "I 4I "         # num_engines, eng_state[4]
    "4f 4f 4f 4f 4f 4f 4f 4f 4f "  # rpm fuel_flow fuel_px egt cht mp_osi tit oil_temp oil_px
    "I 4f "         # num_tanks fuel_quantity[4]
    "I 3I "         # num_wheels wow[3]
    "3f 3f 3f "     # gear_pos gear_steer gear_compression
    "I i f "        # cur_time warp visibility(m)
    "f f f f f f f f f f"  # elevator elev_trim l_flap r_flap l_ail r_ail rudder nose_wheel speedbrake spoilers
)

assert struct.calcsize(_FDM_FMT) == 408, \
    "FGNetFDM v24 must be 408 bytes -- packing definition is wrong"


def pack_fgnetfdm(fdm: "jsbsim.FGFDMExec") -> bytes:
    """Encode current JSBSim state as FGNetFDM v24. [c] hand-coded, see header."""
    g = fdm.get_property_value
    z4 = (0.0, 0.0, 0.0, 0.0)
    return struct.pack(
        _FDM_FMT,
        _FDM_VERSION, 0,
        math.radians(g("position/long-gc-deg")),
        math.radians(g("position/lat-gc-deg")),
        g("position/h-sl-ft") * FT2M,
        g("position/h-agl-ft") * FT2M,
        math.radians(g("attitude/phi-deg")),
        math.radians(g("attitude/theta-deg")),
        math.radians(g("attitude/psi-deg")),
        g("aero/alpha-rad"), g("aero/beta-rad"),
        g("velocities/p-rad_sec"), g("velocities/q-rad_sec"),
        g("velocities/r-rad_sec"),
        g("velocities/vc-kts"),
        g("velocities/h-dot-fps"),
        g("velocities/v-north-fps"), g("velocities/v-east-fps"),
        g("velocities/v-down-fps"),
        g("velocities/u-fps"), g("velocities/v-fps"), g("velocities/w-fps"),
        g("accelerations/pilot-accel-x-ft_sec2"),
        g("accelerations/pilot-accel-y-ft_sec2"),
        g("accelerations/pilot-accel-z-ft_sec2"),
        0.0,                                   # stall_warning
        0.0,                                   # slip_deg
        1, 1, 0, 0, 0,                         # num_engines=1, eng_state
        *z4, *z4, *z4, *z4, *z4, *z4, *z4, *z4, *z4,   # engine arrays
        1, g("propulsion/tank/contents-lbs"), 0.0, 0.0, 0.0,  # tanks
        3, 0, 0, 0,                            # wheels, wow[3]
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,   # gear arrays
        int(time.time()), 0,
        25000.0,                               # visibility (m) [c]
        g("fcs/elevator-pos-norm"), 0.0,
        g("fcs/flap-pos-norm"), g("fcs/flap-pos-norm"),
        g("fcs/left-aileron-pos-norm"), g("fcs/right-aileron-pos-norm"),
        g("fcs/rudder-pos-norm"), 0.0, 0.0, 0.0,
    )


# ---------------------------------------------------------------------------
# ThreadPhysics
# ---------------------------------------------------------------------------

class ThreadPhysics(threading.Thread):
    """The PCS physics thread (Thread 2 in the HL architecture diagram).

    Port realization [b]:
      i_HotasControlIn  = hotas_in   (queue.Queue of HotasControls)
      i_OwnTelemetryOut = telemetry_out (queue.Queue of OwnshipState;
                          latest-value, maxsize=1 recommended)
      e_nativeJSBSimFdmOut = UDP -> fdm_host:fdm_port (default 5550 [a])
    """

    def __init__(self,
                 hotas_in: queue.Queue,
                 telemetry_out: queue.Queue,
                 aircraft: str = "f16",
                 ic: InitialConditions | None = None,
                 fdm_host: str = ports.LOCALHOST,
                 fdm_port: int = ports.UDP_NATIVE_FDM,
                 jsbsim_root: str | None = None):
        super().__init__(name="ThreadPhysics", daemon=True)
        self._hotas_in = hotas_in
        self._telemetry_out = telemetry_out
        self._aircraft = aircraft
        self._ic = ic or InitialConditions()
        self._fdm_addr = (fdm_host, fdm_port)
        self._jsbsim_root = jsbsim_root

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._controls = HotasControls()       # last received input
        self._seq = 0
        self._fdm_decimation = 2               # [a] 120/2 = 60 Hz visual
        self._speedbrake = 0.0                 # toggled by btn4 [a: legacy]
        self._prev_btn4 = False
        self._gear_down = True                 # toggled by btn2 [a: customer]
        self._prev_btn2 = False
        self._engine_on = False                # [a: L2SYS-REQ-01.07]
        self._parking_brake = True             # [c] ON at spawn (ref GUI)
        self._terrain_target_m = None          # FG ground-elev feedback
        self._fdm: jsbsim.FGFDMExec | None = None

        # SSF state (thread-safe primitives)
        self._running = threading.Event()      # ssfStart/Stop
        self._alive = threading.Event()        # thread lifetime
        self._alive.set()
        self._cmd_lock = threading.Lock()
        self._pending_reset: InitialConditions | None = None
        self._freeze_fuel = False
        self._frozen_fuel_lbs: float | None = None

    # ------------------------------------------------------------------
    # Group 1: initializationPhase (aperiodic)  [a: TA action group]
    # ------------------------------------------------------------------

    def _init_fdm(self) -> None:                       # initFDM
        self._fdm = jsbsim.FGFDMExec(self._jsbsim_root)
        self._fdm.set_debug_level(0)
        if not self._fdm.load_model(self._aircraft):
            raise RuntimeError(f"JSBSim could not load aircraft "
                               f"{self._aircraft!r}")
        # [c] some bundled models carry a CSV output directive; we do our
        # own telemetry, so silence JSBSim's file output entirely
        self._fdm.disable_output()
        self._fdm.set_dt(PHYSICS_DT)                   # [a] 120 Hz

    def _set_initial_conditions(self) -> None:         # setInitialConditions
        ic, f = self._ic, self._fdm
        f["ic/lat-gc-deg"] = ic.lat_deg
        f["ic/long-gc-deg"] = ic.lon_deg
        f["ic/psi-true-deg"] = ic.heading_deg
        if ic.on_ground:
            # ground start at field elevation [a-pending: L2SYS-REQ-01.04]
            f["ic/terrain-elevation-ft"] = ic.terrain_elev_m * M2FT
            f["ic/h-agl-ft"] = 4.4      # [c] F16 static gear height, tuned
            f["ic/vc-kts"] = 0.0
            # [b: bug fix 2026-07-12] clean-slate ground state: gear down,
            # PRK BRK ON (holds the aircraft against residual idle thrust
            # during engine spool-down), airbrake in, fuel freeze off,
            # terrain feedback re-armed from the airport elevation
            self._gear_down = True
            self._parking_brake = True
            self._speedbrake = 0.0
            self._freeze_fuel = False
            self._frozen_fuel_lbs = None
            self._terrain_target_m = None
        else:
            f["ic/h-sl-ft"] = ic.alt_m * M2FT
            f["ic/vc-kts"] = ic.airspeed_kts
            f["ic/gamma-deg"] = 0.0                    # level flight [c]
        if not f.run_ic():
            raise RuntimeError("JSBSim initial-conditions run failed")
        # Engine policy [a: L2SYS-REQ-01.07, alloc VirtualOwnship]:
        # ground start -> engine OFF, no fuel consumption, awaiting the
        # pilot's engine-start command. Airborne (re)start -> engine ON
        # ([b]: an airborne teleport with a dead engine would instantly
        # violate the flight condition the IC establishes).
        if ic.on_ground:
            self._engine_on = False
            f["propulsion/cutoff_cmd"] = 1.0
        else:
            self._engine_on = True
            self._parking_brake = False        # airborne: brakes off [b]
            f["propulsion/cutoff_cmd"] = 0.0
            f["propulsion/set-running"] = -1

    def _update_environment(self) -> None:             # updateEnvironment
        f = self._fdm
        f["atmosphere/wind-north-fps"] = 0.0           # calm defaults [c]
        f["atmosphere/wind-east-fps"] = 0.0
        f["atmosphere/wind-down-fps"] = 0.0

    # ------------------------------------------------------------------
    # Group 2: cyclicExecutionLoop (periodic, 120 Hz)  [a: TA action group]
    # ------------------------------------------------------------------

    def _acquire_control_inputs(self) -> None:         # acquireControlInputs
        # [c] latest-value semantics: drain queue, keep newest
        try:
            while True:
                self._controls = self._hotas_in.get_nowait()
        except queue.Empty:
            pass

    def _map_inputs_to_fdm(self) -> None:              # mapInputsToFDM
        c, f = self._controls, self._fdm
        f["fcs/aileron-cmd-norm"] = c.roll
        f["fcs/elevator-cmd-norm"] = -c.pitch          # [c] stick-back = nose-up
        f["fcs/rudder-cmd-norm"] = c.yaw
        # [b: bug fix 2026-07-12] JSBSim models commonly route nose-wheel
        # steering from fcs/steer-cmd-norm, not the rudder channel --
        # drive both from yaw so taxi steering works.
        f["fcs/steer-cmd-norm"] = c.yaw
        f["fcs/throttle-cmd-norm"] = c.throttle
        # Button map: btn2 = landing gear toggle [a: customer 2026-07-11],
        # btn4 = airbrake toggle, btn6 = wheel brakes hold [a: legacy]
        btn4 = c.switches.get("btn4", False)
        if btn4 and not self._prev_btn4:
            self._speedbrake = 1.0 - self._speedbrake
        self._prev_btn4 = btn4
        f["fcs/speedbrake-cmd-norm"] = self._speedbrake
        # btn2 = landing gear TOGGLE [a: customer decision 2026-07-11]
        btn2 = c.switches.get("btn2", False)
        if btn2 and not self._prev_btn2:
            self._gear_down = not self._gear_down
        self._prev_btn2 = btn2
        f["gear/gear-cmd-norm"] = 1.0 if self._gear_down else 0.0
        brake = 1.0 if (c.switches.get("btn6", False)
                        or self._parking_brake) else 0.0
        f["fcs/left-brake-cmd-norm"] = brake
        f["fcs/right-brake-cmd-norm"] = brake

    def set_terrain_elevation(self, elev_m: float) -> None:
        """[b: bug fix 2026-07-12] feed FlightGear's scenery ground
        elevation back into JSBSim so both worlds share one terrain
        (prevents taxiing 'underground' in the OTW view). Realizes the
        TA ground-elev-m loop (FG httpd 8080 -> PCS -> physics)."""
        self._terrain_target_m = elev_m

    _TERRAIN_SLEW_FT = 1.0     # [c] max ft per 120Hz frame (gear-safe)

    def _apply_terrain_feedback(self) -> None:
        if self._terrain_target_m is None:
            return
        cur = self._fdm["position/terrain-elevation-asl-ft"]
        tgt = self._terrain_target_m * M2FT
        d = tgt - cur
        if abs(d) > self._TERRAIN_SLEW_FT:
            d = self._TERRAIN_SLEW_FT if d > 0 else -self._TERRAIN_SLEW_FT
        self._fdm["position/terrain-elevation-asl-ft"] = cur + d

    def _execute_physics_step(self) -> None:           # executePhysicsStep
        self._apply_terrain_feedback()
        if self._freeze_fuel:                          # ssfFreezeFuelConsupmtion
            if self._frozen_fuel_lbs is None:
                self._frozen_fuel_lbs = self._fdm[
                    "propulsion/tank/contents-lbs"]
            self._fdm["propulsion/tank/contents-lbs"] = self._frozen_fuel_lbs
        self._fdm.run()

    def _extract_telemetry(self) -> OwnshipState:      # extractTelemetry
        g = self._fdm.get_property_value
        self._seq += 1
        return OwnshipState(
            seq=self._seq,
            sim_time_s=g("simulation/sim-time-sec"),
            lat_deg=g("position/lat-gc-deg"),
            lon_deg=g("position/long-gc-deg"),
            alt_m=g("position/h-sl-ft") * FT2M,
            heading_deg=g("attitude/psi-deg"),
            pitch_deg=g("attitude/theta-deg"),
            roll_deg=g("attitude/phi-deg"),
            groundspeed_ms=g("velocities/vg-fps") * FT2M,
            vertical_speed_ms=g("velocities/h-dot-fps") * FT2M,
        )

    def _transmit_telemetry_signal(self, tm: OwnshipState) -> None:
        # trasmitTelemetrySignal (sic) -> i_OwnTelemetryOut
        # [c] latest-value: drop stale entry if consumer is slower
        try:
            self._telemetry_out.put_nowait(tm)
        except queue.Full:
            try:
                self._telemetry_out.get_nowait()
            except queue.Empty:
                pass
            try:
                self._telemetry_out.put_nowait(tm)
            except queue.Full:
                pass

    def _transmit_visual_telemetry(self) -> None:
        # trasmitVisualTelemetry (sic) -> e_nativeJSBSimFdmOut (UDP 5550 [a])
        # decimated to 60 Hz to match the FG native-fdm read rate
        # [a: legacy launch cmd "socket,in,60"]
        if self._seq % self._fdm_decimation == 0:
            self._sock.sendto(pack_fgnetfdm(self._fdm), self._fdm_addr)

    # ------------------------------------------------------------------
    # SSF interface (called from the UI thread)  [b: UIM action mapping]
    # ------------------------------------------------------------------

    def ssf_start(self) -> None:                       # ssfStartSimulation
        self._running.set()

    def ssf_stop(self) -> None:                        # ssfStopSimulation
        self._running.clear()

    def ssf_reset(self, ic: InitialConditions | None = None) -> None:
        with self._cmd_lock:                           # ssfResetSimulation
            self._pending_reset = ic or self._ic

    def ssf_set_own_position(self, lat_deg: float, lon_deg: float,
                             alt_m: float, heading_deg: float = 0.0,
                             airspeed_kts: float = 120.0) -> None:
        self.ssf_reset(InitialConditions(                 # ssfSetOwnPosition
            lat_deg, lon_deg, alt_m, heading_deg, airspeed_kts))

    # --- GUI System Controls setters (thread-safe: plain flag writes
    #     consumed by the 120 Hz loop) [b: UI-1, mirrors HOTAS toggles] ---
    def set_parking_brake(self, on: bool) -> None:
        self._parking_brake = on

    def set_gear(self, down: bool) -> None:
        self._gear_down = down

    def set_airbrake(self, out: bool) -> None:
        self._speedbrake = 1.0 if out else 0.0

    @property
    def parking_brake(self) -> bool:
        return self._parking_brake

    @property
    def gear_down(self) -> bool:
        return self._gear_down

    @property
    def airbrake_out(self) -> bool:
        return self._speedbrake > 0.5

    def set_engine(self, on: bool) -> None:
        """Engine master ON/OFF [a: L2SYS-REQ-01.07]. ON: clears
        fuel cutoff and spools the turbine to idle (fuel flow commences,
        thrust then follows the physical throttle). OFF: fuel cutoff,
        spool-down to zero, no consumption."""
        self._engine_on = on
        if on:
            self._fdm["propulsion/cutoff_cmd"] = 0.0
            self._fdm["propulsion/set-running"] = -1   # spool to idle
        else:
            self._fdm["propulsion/cutoff_cmd"] = 1.0

    @property
    def engine_on(self) -> bool:
        return self._engine_on

    def ssf_freeze_fuel(self, freeze: bool) -> None:
        self._freeze_fuel = freeze                     # ssfFreezeFuelConsupmtion
        if not freeze:
            self._frozen_fuel_lbs = None

    def shutdown(self) -> None:
        self._alive.clear()

    # ------------------------------------------------------------------
    # Thread body: init once, then paced 120 Hz loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        # initializationPhase
        self._init_fdm()
        self._set_initial_conditions()
        self._update_environment()
        self._running.set()                            # [c] auto-start

        t_next = time.monotonic()
        while self._alive.is_set():
            # handle SSF reset requests between frames
            with self._cmd_lock:
                pending, self._pending_reset = self._pending_reset, None
            if pending is not None:
                self._ic = pending
                self._set_initial_conditions()
                self._update_environment()
                # [b: bug fix 2026-07-12] publish the new state once even
                # while paused: cockpit, map and FG teleport immediately
                tm = self._extract_telemetry()
                self._transmit_telemetry_signal(tm)
                self._sock.sendto(pack_fgnetfdm(self._fdm), self._fdm_addr)

            if not self._running.is_set():
                time.sleep(0.05)
                t_next = time.monotonic()
                continue

            # cyclicExecutionLoop
            self._acquire_control_inputs()
            self._map_inputs_to_fdm()
            self._execute_physics_step()
            tm = self._extract_telemetry()
            self._transmit_telemetry_signal(tm)
            self._transmit_visual_telemetry()

            # frame pacing to 120 Hz [a]
            t_next += PHYSICS_DT
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                t_next = time.monotonic()              # overrun: resync [c]

        self._sock.close()


# ---------------------------------------------------------------------------
# Standalone smoke test:  python -m flightsim.pcs.physics
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    hotas_q: queue.Queue = queue.Queue()
    telem_q: queue.Queue = queue.Queue(maxsize=1)
    phys = ThreadPhysics(hotas_q, telem_q)
    phys.start()
    print("ThreadPhysics running; 5 s of level flight with slight climb input")
    hotas_q.put(HotasControls(pitch=0.15, throttle=0.9))
    t0 = time.monotonic()
    while time.monotonic() - t0 < 5.0:
        tm = telem_q.get()
        if tm.seq % 120 == 0:
            print(f"t={tm.sim_time_s:5.1f}s alt={tm.alt_m:7.1f}m "
                  f"hdg={tm.heading_deg:5.1f} gs={tm.groundspeed_ms:5.1f}m/s "
                  f"vs={tm.vertical_speed_ms:+5.2f}m/s")
    phys.shutdown()
    phys.join(timeout=2)
    print("done.")