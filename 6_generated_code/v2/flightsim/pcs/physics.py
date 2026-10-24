"""ThreadPhysics -- the Virtual Ownship. [a: fsim_TA_PCS_ThreadPhysics.sysml]

The TA gives this thread two action groups, realized here as two methods:

    initializationPhase (aperiodic)   -> PhysicsThread.initialize()
        initFDM                       -> _init_fdm()
        setInitialConditions          -> _set_initial_conditions()
        updateEnvironment             -> _update_environment()

    cyclicExecutionLoop (periodic)    -> PhysicsThread.step()
        acquireControlInputs          -> _acquire_control_inputs()
        mapInputsToFDM                -> _write_controls()
        executePhysicsStep            -> _execute_physics_step()
        extractTelemetry              -> _extract_telemetry()
        trasmitTelemetrySignal        -> _transmit_telemetry()
        trasmitVisualTelemetry        -> _transmit_visual()

[a: L2SYS-REQ-01.02] The system shall comprise a Virtual Ownship.
[a: L2SYS-REQ-01.02b] 120 Hz from received control inputs.
[a: L2SYS-REQ-01.04] Initialize on ground at a user-selected airport.
[a: L2SYS-REQ-01.06b] Engine start/stop. [a: L2SYS-REQ-01.06c] Park brake.
[a: L2SYS-REQ-01.06d] Air brake. [a: L2SYS-REQ-01.06e] Landing gear.
[a: L2SYS-REQ-01.07] Engine initializes OFF with zero fuel consumption.

**Every FDM property written or read below carries its D17 measurement at the
implementing line.** The measurements are in docs/FDM_MEASUREMENTS.md; the four
that changed the implementation are M-A (elevator inverted), M-B (rudder
inverted), M-C (steering inverted) and M-D (the ground initial condition).

Nothing here requires FlightGear: the FDM datagram is written whether or not
anything is listening, and the terrain poll is inactive when FlightGear is not
configured. [a: R-5] [a: D9] [a: Sec.3 criterion 6]
"""

from __future__ import annotations

import math
import socket
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from flightsim.common import ports
from flightsim.common.messages import OwnshipState, SeqCounter
from flightsim.pcs import fdm_net
from flightsim.pcs.airports import Airport, SEED_AIRPORTS

# ---------------------------------------------------------------------------
# Rates and unit conversions
# ---------------------------------------------------------------------------

#: [a: L2SYS-REQ-01.02b] "at a 120Hz frequency"
PHYSICS_HZ = 120.0
PHYSICS_DT = 1.0 / PHYSICS_HZ

#: [a: D4] "decimated to about 60 Hz". 120 / 2 = 60.
FDM_DECIMATION = 2

#: [a: D9] "Poll FlightGear ground elevation at 2 Hz"
TERRAIN_POLL_HZ = 2.0
TERRAIN_POLL_PERIOD_S = 1.0 / TERRAIN_POLL_HZ

#: [a: D9] "slewed at most 1 ft per 120 Hz frame"
TERRAIN_SLEW_FT_PER_FRAME = 1.0

#: [a: D8] "Engine start is refused above 10 % throttle; exactly 10 % starts."
ENGINE_START_MAX_THROTTLE = 0.10

#: [a: D6] "JSBSim model f16"
JSBSIM_MODEL = "f16"

#: MEASURED, M-D. Initializing at ic/h-agl-ft = 3.0 ft compresses the oleos so
#: gear/wow reads 1 and ground speed reads 0.0000 ft/s AT t0, which is what D8
#: requires. At 6.0 ft, gear/wow reads 0 at t0. See docs/FDM_MEASUREMENTS.md.
GROUND_START_AGL_FT = 3.0

FT_PER_M = 3.280839895013123
M_PER_FT = 0.3048
MPS_PER_FPS = 0.3048
KTS_PER_FPS = 1.0 / 1.6878098571011957


# ---------------------------------------------------------------------------
# Control inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ControlCommand:
    """What the pilot side commands. **Signs are pilot-side.** [a: D10] [a: D1]

    pitch +1 is nose up  [a: D10] "Drag-down = stick back = pitch +1"
    roll  +1 is right    [b: D1]
    yaw   +1 is nose right -- [c], no Decision fixes the yaw sign; what would
          settle it is a stated yaw-sign requirement. Both FDM properties it
          drives are inverted relative to this (measurements M-B and M-C), and
          the negation happens once, in `_write_controls`.
    """

    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    throttle: float = 0.0
    #: [a: D7] "btn6 = momentary wheel brake"
    wheel_brake: float = 0.0
    #: [a: D7] "btn2 = landing-gear toggle" -- the receiver's toggled result
    gear_down: bool = True
    #: [a: D7] "btn4 = air-brake toggle" -- the receiver's toggled result
    air_brake: bool = False
    #: [a: L2SYS-REQ-01.06b] ENGINE toggle. [a: D21] the COMMANDED state.
    engine_on: bool = False
    #: [a: L2SYS-REQ-01.06c] PARK BRAKE toggle.
    parking_brake: bool = True
    trim_pitch: float = 0.0
    trim_roll: float = 0.0
    trim_yaw: float = 0.0


class CommandHolder:
    """Thread-safe latest-command holder.

    Realizes `threadPhysics.i_HotasControlIn`
    [a: fsim_TA_PCS.sysml] `bind threadFlightSim.i_HotasControlOut =
    threadPhysics.i_HotasControlIn`. A holder rather than a queue: the physics
    wants the *latest* command each frame, and a queue of stale commands would
    make the aircraft fly the past. [b]
    """

    def __init__(self, initial: ControlCommand | None = None) -> None:
        self._lock = threading.Lock()
        self._value = initial or ControlCommand()

    def set(self, command: ControlCommand) -> None:
        with self._lock:
            self._value = command

    def update(self, **kw) -> ControlCommand:
        with self._lock:
            self._value = replace(self._value, **kw)
            return self._value

    def get(self) -> ControlCommand:
        with self._lock:
            return self._value


class StateHolder:
    """Thread-safe latest-telemetry holder.

    Realizes `threadPhysics.i_OwnTelemetryOut`
    [a: fsim_TA_PCS.sysml] `bind threadPhysics.i_OwnTelemetryOut =
    threadFlightSim.i_OwnTelemetryIn`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: OwnshipState | None = None
        self._count = 0

    def set(self, state: OwnshipState) -> None:
        with self._lock:
            self._value = state
            self._count += 1

    def get(self) -> OwnshipState | None:
        with self._lock:
            return self._value

    @property
    def count(self) -> int:
        with self._lock:
            return self._count


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------
@dataclass
class Snapshot:
    """Everything SSF SAVE SNAPSHOT must restore on the physics side.
    [a: L2SYS-REQ-01.10b] "SAVE SNAPSHOT / LOAD SNAPSHOT: save/restore full
    simulation state."
    """

    icao: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    heading_deg: float
    pitch_deg: float
    roll_deg: float
    speed_mps: float
    fuel_lbs: float
    engine_running: bool
    parking_brake: bool
    air_brake: bool
    gear_down: bool
    fuel_freeze: bool
    sim_time_s: float


class PhysicsThread:
    """The 120 Hz flight-dynamics loop. [a: fsim_TA_PCS_ThreadPhysics.sysml]"""

    def __init__(
        self,
        airport: Airport | None = None,
        fdm_host: str = ports.LOOPBACK,
        fdm_port: int = ports.PCS_TO_FLIGHTGEAR_FDM,
        sock: socket.socket | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        model: str = JSBSIM_MODEL,
        fdm_factory: Callable[[], object] | None = None,
        ground_elev_source: Callable[[], float | None] | None = None,
        emit_fdm: bool = True,
    ) -> None:
        self.airport = airport or SEED_AIRPORTS[0]
        self.model = model
        self.commands = CommandHolder()
        self.telemetry = StateHolder()
        self.stop_event = threading.Event()

        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._fdm_factory = fdm_factory
        self._fdm = None

        self._sock = sock
        self._owns_socket = sock is None and emit_fdm
        self._emit_fdm = emit_fdm
        self._fdm_host = fdm_host
        self._fdm_port = fdm_port

        self._seq = SeqCounter()
        self._frame = 0
        self.fdm_datagrams_sent = 0

        # -- run state ----------------------------------------------------
        self.running = False           # RUN SIM / PAUSE SIM [a: 01.10b]
        self.fuel_freeze = False       # FUEL FREEZE [a: 01.10b]
        self._frozen_fuel_lbs: float | None = None
        self._engine_commanded = False   # [a: D21]
        self.messages: list[str] = []

        # -- terrain [a: D9] ----------------------------------------------
        self.ground_elev_source = ground_elev_source
        self._terrain_target_ft: float | None = None
        self._next_terrain_poll = 0.0
        self.terrain_polls = 0
        self.terrain_poll_failures = 0

    # ------------------------------------------------------------------
    # initializationPhase [a: fsim_TA_PCS_ThreadPhysics.sysml]
    # ------------------------------------------------------------------
    def initialize(self, airport: Airport | None = None) -> None:
        """initFDM -> setInitialConditions -> updateEnvironment."""
        if airport is not None:
            self.airport = airport
        self._init_fdm()
        self._set_initial_conditions()
        self._update_environment()
        self._frame = 0
        self.fdm_datagrams_sent = 0
        self._next_terrain_poll = self._clock()
        self._extract_and_publish()

    def _init_fdm(self):
        """initFDM. [a: D6] JSBSim model f16."""
        if self._fdm_factory is not None:
            self._fdm = self._fdm_factory()
            return self._fdm
        import jsbsim  # noqa: PLC0415 -- kept out of import time so this module
                       # can be imported for inspection without the FDM present

        fdm = jsbsim.FGFDMExec(None)
        fdm.set_debug_level(0)
        if not fdm.load_model(self.model):
            raise RuntimeError(f"could not load JSBSim model {self.model!r}")
        self._fdm = fdm
        return fdm

    def _set_initial_conditions(self) -> None:
        """setInitialConditions.

        [a: L2SYS-REQ-01.04] on the ground at the selected airport.
        [a: D8] "Spawn on the runway with the engine off, parking brake on,
        gear down. At t0 weight is on the wheels and ground speed is zero."
        """
        f = self._fdm
        a = self.airport
        # MEASURED: position/lat-gc-deg is degrees, positive north;
        # position/long-gc-deg is degrees, positive east. (M: read back
        # 45.445100 / 9.276700 after initializing at those values.)
        f["ic/lat-gc-deg"] = a.lat_deg
        f["ic/long-gc-deg"] = a.lon_deg
        # MEASURED: ic/terrain-elevation-ft is FEET above sea level, positive
        # up. D13's elevation is metres, so it is converted here.
        f["ic/terrain-elevation-ft"] = a.elevation_m * FT_PER_M
        # MEASURED, M-D: 3.0 ft compresses the oleos so gear/wow reads 1 at t0.
        f["ic/h-agl-ft"] = GROUND_START_AGL_FT
        # MEASURED: attitude/psi-deg is degrees true, increasing clockwise.
        f["ic/psi-true-deg"] = a.runway_hdg_deg
        f["ic/vt-fps"] = 0.0            # [a: D8] ground speed is zero at t0
        f["ic/gamma-deg"] = 0.0
        f.run_ic()
        f.set_dt(PHYSICS_DT)            # [a: L2SYS-REQ-01.02b]
        try:
            f.set_sim_time(0.0)
        except Exception:               # pragma: no cover - API variance
            pass

        # [a: D8] engine off, parking brake on, gear down at t0.
        self._engine_commanded = False
        self.commands.set(ControlCommand(
            engine_on=False, parking_brake=True, gear_down=True,
            air_brake=False, throttle=0.0,
        ))
        self.fuel_freeze = False
        self._frozen_fuel_lbs = None
        # MEASURED: gear/gear-cmd-norm is normalized [0, 1] with 1 = DOWN.
        f["gear/gear-cmd-norm"] = 1.0
        # MEASURED: fcs/left-brake-cmd-norm and fcs/right-brake-cmd-norm are
        # normalized [0, 1] with 1 = full brake.
        f["fcs/left-brake-cmd-norm"] = 1.0
        f["fcs/right-brake-cmd-norm"] = 1.0
        f["fcs/throttle-cmd-norm"] = 0.0
        # MEASURED: fcs/speedbrake-cmd-norm is normalized [0, 1], 1 = extended.
        f["fcs/speedbrake-cmd-norm"] = 0.0

    def _update_environment(self) -> None:
        """updateEnvironment. [a: D9] terrain elevation from the airport row
        until FlightGear supplies one."""
        f = self._fdm
        # MEASURED: position/terrain-elevation-asl-ft is FEET above sea level,
        # positive up (write elevation+100 ft, step, read back: 353 -> 453).
        elev_ft = self.airport.elevation_m * FT_PER_M
        f["position/terrain-elevation-asl-ft"] = elev_ft
        self._terrain_target_ft = elev_ft

    # ------------------------------------------------------------------
    # cyclicExecutionLoop [a: fsim_TA_PCS_ThreadPhysics.sysml]
    # ------------------------------------------------------------------
    def step(self) -> OwnshipState:
        """One 120 Hz frame. [a: L2SYS-REQ-01.02b]"""
        command = self._acquire_control_inputs()
        self._write_controls(command)
        self._poll_terrain()
        self._slew_terrain()
        self._execute_physics_step()
        state = self._extract_telemetry(command)
        self._transmit_telemetry(state)
        self._transmit_visual(state)
        self._frame += 1
        return state

    def _acquire_control_inputs(self) -> ControlCommand:
        """acquireControlInputs."""
        return self.commands.get()

    def _write_controls(self, c: ControlCommand) -> None:
        """mapInputsToFDM.

        **This is the only place in the package where a control sign is
        reconciled.** Each negation cites the measurement that requires it.
        [a: D17]
        """
        f = self._fdm

        # MEASURED: fcs/aileron-cmd-norm is normalized [-1, 1] and POSITIVE
        # rolls RIGHT (+0.3 -> phi +93.5 deg). The pilot side is also
        # positive-right, so NO negation.
        f["fcs/aileron-cmd-norm"] = _clip(c.roll, -1.0, 1.0)

        # MEASURED, M-A: fcs/elevator-cmd-norm is INVERTED -- a NEGATIVE
        # command pitches the nose UP (-0.3 -> theta +13.66 deg, climbing at
        # +112.9 ft/s). D10 fixes the pilot side as "drag-down = stick back =
        # pitch +1" = nose up, so D17's rule applies: the Decision governs the
        # pilot side and the implementation reconciles here.
        f["fcs/elevator-cmd-norm"] = _clip(-c.pitch, -1.0, 1.0)

        # MEASURED, M-B: fcs/rudder-cmd-norm is INVERTED -- +0.4 gives a yaw
        # rate of -0.40 deg/s, i.e. nose LEFT.
        # MEASURED, M-C: fcs/steer-cmd-norm is inverted the same way -- +1.0
        # taxied heading 180 deg -> 99 deg, i.e. LEFT.
        # [a: D7] "Yaw drives both fcs/rudder-cmd-norm and fcs/steer-cmd-norm."
        yaw_fdm = _clip(-c.yaw, -1.0, 1.0)
        f["fcs/rudder-cmd-norm"] = yaw_fdm
        f["fcs/steer-cmd-norm"] = yaw_fdm

        # MEASURED: fcs/throttle-cmd-norm is normalized [0, 1], monotonically
        # increasing thrust (0.0 -> 872 lb, 1.0 -> 27 659 lb).
        f["fcs/throttle-cmd-norm"] = _clip(c.throttle, 0.0, 1.0)

        # [a: L2SYS-REQ-01.06e] Landing Gear (UP/DOWN).
        # MEASURED: gear/gear-cmd-norm 1 = DOWN, 0 = UP (cmd 0 -> pos 0.000,
        # cmd 1 -> pos 1.000).
        f["gear/gear-cmd-norm"] = 1.0 if c.gear_down else 0.0

        # [a: L2SYS-REQ-01.06d] Air Brake (ON/OFF).
        # MEASURED: fcs/speedbrake-cmd-norm 1 = extended.
        f["fcs/speedbrake-cmd-norm"] = 1.0 if c.air_brake else 0.0

        # [a: L2SYS-REQ-01.06c] Park Brake (ON/OFF), and
        # [a: D7] "btn6 = momentary wheel brake".
        # MEASURED: fcs/left|right-brake-cmd-norm normalized [0, 1], 1 = full.
        brake = 1.0 if c.parking_brake else _clip(c.wheel_brake, 0.0, 1.0)
        f["fcs/left-brake-cmd-norm"] = brake
        f["fcs/right-brake-cmd-norm"] = brake

        # trims -- MEASURED: present in the catalogue, normalized [-1, 1].
        f["fcs/pitch-trim-cmd-norm"] = _clip(c.trim_pitch, -1.0, 1.0)
        f["fcs/roll-trim-cmd-norm"] = _clip(c.trim_roll, -1.0, 1.0)
        f["fcs/yaw-trim-cmd-norm"] = _clip(c.trim_yaw, -1.0, 1.0)

        # [a: L2SYS-REQ-01.06b] engine start/stop, gated by D8.
        self._apply_engine_command(c)

    def _apply_engine_command(self, c: ControlCommand) -> None:
        """[a: L2SYS-REQ-01.06b] [a: L2SYS-REQ-01.07] [a: D8]

        "Engine start is refused above 10 % throttle; exactly 10 % starts."
        [a: D21] `engine_running` is the state COMMANDED by the ENGINE toggle.
        """
        f = self._fdm
        if c.engine_on and not self._engine_commanded:
            if c.throttle > ENGINE_START_MAX_THROTTLE:
                self.messages.append(
                    f"ENGINE START REFUSED: throttle {c.throttle * 100:.0f}% "
                    f"is above {ENGINE_START_MAX_THROTTLE * 100:.0f}%"
                )
                self.commands.update(engine_on=False)
                return
            # MEASURED: propulsion/set-running takes an engine index, -1 for
            # every engine; writing -1 raised n2 from 0 to 62.4 % and fuel
            # began to fall.
            f["propulsion/set-running"] = -1
            self._engine_commanded = True
            self.messages.append("ENGINE START")
        elif not c.engine_on and self._engine_commanded:
            f["propulsion/cutoff_cmd"] = 1
            f["propulsion/set-running"] = 0
            try:
                f["propulsion/engine/set-running"] = 0
            except Exception:           # pragma: no cover - API variance
                pass
            self._engine_commanded = False
            self.messages.append("ENGINE STOP")

    def _execute_physics_step(self) -> None:
        """executePhysicsStep. [a: L2SYS-REQ-01.02b]"""
        self._fdm.run()
        if self.fuel_freeze:
            # [a: L2SYS-REQ-01.10b] "FUEL FREEZE: hold tank contents constant."
            self._hold_fuel()

    def _hold_fuel(self) -> None:
        f = self._fdm
        if self._frozen_fuel_lbs is None:
            self._frozen_fuel_lbs = float(f["propulsion/total-fuel-lbs"])
        # MEASURED: propulsion/tank/contents-lbs is pounds. Restoring the tanks
        # to their frozen totals holds the aggregate constant.
        target = self._frozen_fuel_lbs
        current = float(f["propulsion/total-fuel-lbs"])
        if current <= 0.0:
            return
        scale = target / current
        for i in range(8):
            key = "propulsion/tank/contents-lbs" if i == 0 else f"propulsion/tank[{i}]/contents-lbs"
            try:
                value = float(f[key])
            except Exception:
                break
            if value:
                f[key] = value * scale

    def _extract_telemetry(self, c: ControlCommand) -> OwnshipState:
        """extractTelemetry. Every field of D19, in D19's units. [a: D19]"""
        f = self._fdm
        # MEASURED units, each recorded in docs/FDM_MEASUREMENTS.md:
        #   position/h-sl-meters      metres ASL, positive up
        #   position/h-agl-ft         feet AGL, positive up
        #   attitude/psi|theta|phi    degrees; psi 0..360 clockwise,
        #                             theta positive nose up,
        #                             phi positive right wing down
        #   velocities/q-rad_sec      RADIANS per second, positive nose up
        #   velocities/vtrue-fps      ft/s TRUE airspeed  [a: D20]
        #   velocities/h-dot-fps      ft/s, positive UP
        #   propulsion/engine/n2      per cent
        #   propulsion/total-fuel-lbs pounds
        #   gear/wow                  1 when weight is on the wheels
        alt_m = float(f["position/h-sl-meters"])
        agl_m = float(f["position/h-agl-ft"]) * M_PER_FT
        return OwnshipState(
            seq=self._seq.next(),
            sim_time_s=float(f.get_sim_time()),
            lat_deg=float(f["position/lat-gc-deg"]),
            lon_deg=float(f["position/long-gc-deg"]),
            alt_m=alt_m,
            agl_m=agl_m,
            heading_deg=float(f["attitude/psi-deg"]) % 360.0,
            pitch_deg=float(f["attitude/theta-deg"]),
            roll_deg=float(f["attitude/phi-deg"]),
            pitch_rate_dps=math.degrees(float(f["velocities/q-rad_sec"])),
            # [a: D20, provisional] speed_mps is TRUE airspeed. Measured:
            # vtrue-kts 417.85 against vc-kts 364.79 at 10 000 ft -- they
            # differ, so vtrue is the true-airspeed source and vc is not.
            speed_mps=float(f["velocities/vtrue-fps"]) * MPS_PER_FPS,
            vspeed_mps=float(f["velocities/h-dot-fps"]) * MPS_PER_FPS,
            # [a: D21] measured engine speed drives the RPM gauge only.
            rpm_pct=float(f["propulsion/engine/n2"]),
            fuel_lbs=float(f["propulsion/total-fuel-lbs"]),
            # [a: D21] engine_running is the COMMANDED state, not n2 > 0.
            engine_running=bool(self._engine_commanded),
            parking_brake=bool(c.parking_brake),
            air_brake=bool(c.air_brake),
            gear_down=bool(c.gear_down),
            on_ground=bool(float(f["gear/wow"]) > 0.5),
            # Command fields are reported PILOT-side, so a consumer of
            # OwnshipState never sees the FDM's inverted conventions. [a: D19]
            aileron_cmd=_clip(c.roll, -1.0, 1.0),
            elevator_cmd=_clip(c.pitch, -1.0, 1.0),
            rudder_cmd=_clip(c.yaw, -1.0, 1.0),
            throttle_cmd=_clip(c.throttle, 0.0, 1.0),
            trim_pitch=_clip(c.trim_pitch, -1.0, 1.0),
            trim_roll=_clip(c.trim_roll, -1.0, 1.0),
            trim_yaw=_clip(c.trim_yaw, -1.0, 1.0),
            model=self.model,
        )

    def _transmit_telemetry(self, state: OwnshipState) -> None:
        """trasmitTelemetrySignal -- the internal port to threadFlightSim."""
        self.telemetry.set(state)

    def _transmit_visual(self, state: OwnshipState) -> None:
        """trasmitVisualTelemetry -- FGNetFDM on 5550. [a: D4]

        "decimated to about 60 Hz": one datagram every second 120 Hz frame.
        Written whether or not anything is listening. [a: R-5]
        """
        if not self._emit_fdm:
            return
        if self._frame % FDM_DECIMATION != 0:
            return
        f = self._fdm
        payload = fdm_net.pack(
            lat_deg=state.lat_deg, lon_deg=state.lon_deg, alt_m=state.alt_m,
            agl_m=state.agl_m, phi_deg=state.roll_deg,
            theta_deg=state.pitch_deg, psi_deg=state.heading_deg,
            alpha_deg=float(f["aero/alpha-deg"]),
            beta_deg=float(f["aero/beta-deg"]),
            vcas_kts=float(f["velocities/vc-kts"]),
            climb_rate_fps=float(f["velocities/h-dot-fps"]),
            rpm_pct=state.rpm_pct, fuel_lbs=state.fuel_lbs,
            engine_running=state.engine_running, on_ground=state.on_ground,
            gear_pos=float(f["gear/gear-pos-norm"]),
            elevator=float(f["fcs/elevator-cmd-norm"]),
            left_aileron=float(f["fcs/aileron-cmd-norm"]),
            right_aileron=-float(f["fcs/aileron-cmd-norm"]),
            rudder=float(f["fcs/rudder-cmd-norm"]),
            nose_wheel=float(f["fcs/steer-cmd-norm"]),
            speedbrake=float(f["fcs/speedbrake-pos-norm"]),
            cur_time=int(state.sim_time_s),
        )
        self._send(payload)

    def _send(self, payload: bytes) -> None:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._owns_socket = True
        try:
            self._sock.sendto(payload, (self._fdm_host, self._fdm_port))
            self.fdm_datagrams_sent += 1
        except OSError:
            # Nothing listening is the normal case: FlightGear is optional.
            # [a: Sec.5.3] [a: Sec.3 criterion 6]
            pass

    def _extract_and_publish(self) -> OwnshipState:
        state = self._extract_telemetry(self.commands.get())
        self._transmit_telemetry(state)
        return state

    def apply_commands_now(self) -> OwnshipState:
        """Write the current commands to the FDM and republish, without
        advancing simulation time.

        Needed because the System Controls are usable while flight-frozen: a
        pilot who presses AIR BRAKE with the simulation paused must see the
        state change, and the D8 engine-start gate must be evaluated then too,
        not only on the next 120 Hz frame. [a: L2SYS-REQ-01.06b-e] [a: D8]
        """
        command = self._acquire_control_inputs()
        self._write_controls(command)
        return self._extract_and_publish()

    # ------------------------------------------------------------------
    # Terrain [a: D9]
    # ------------------------------------------------------------------
    def _poll_terrain(self) -> None:
        """[a: D9] "Poll FlightGear ground elevation at 2 Hz ... With no
        FlightGear the poll is inactive and the physics runs unaffected. A
        failed poll is tolerated."
        """
        if self.ground_elev_source is None:
            return                       # no FlightGear configured: inactive
        now = self._clock()
        if now < self._next_terrain_poll:
            return
        self._next_terrain_poll = now + TERRAIN_POLL_PERIOD_S
        self.terrain_polls += 1
        try:
            elev_m = self.ground_elev_source()
        except Exception:
            self.terrain_poll_failures += 1
            return                       # tolerated
        if elev_m is None:
            self.terrain_poll_failures += 1
            return
        self._terrain_target_ft = float(elev_m) * FT_PER_M

    def _slew_terrain(self) -> None:
        """[a: D9] "feed it into the FDM terrain elevation, slewed at most
        1 ft per 120 Hz frame"."""
        if self._terrain_target_ft is None:
            return
        f = self._fdm
        current = float(f["position/terrain-elevation-asl-ft"])
        delta = self._terrain_target_ft - current
        if abs(delta) <= TERRAIN_SLEW_FT_PER_FRAME:
            f["position/terrain-elevation-asl-ft"] = self._terrain_target_ft
        else:
            f["position/terrain-elevation-asl-ft"] = current + math.copysign(
                TERRAIN_SLEW_FT_PER_FRAME, delta
            )

    # ------------------------------------------------------------------
    # SSF backend [a: L2SYS-REQ-01.10b]
    # ------------------------------------------------------------------
    def ssf_run(self) -> None:
        """RUN SIM: start/resume."""
        self.running = True

    def ssf_pause(self) -> None:
        """PAUSE SIM: flight-freeze."""
        self.running = False

    def ssf_reset(self, airport: Airport | None = None) -> OwnshipState:
        """RESET SIM to t0.

        [a: D8] "Reset returns the clean state -- autopilot and fuel freeze
        disengaged -- and publishes one telemetry frame and one FlightGear
        frame even while flight-frozen."

        The autopilot lives in the PCS, so the caller disengages it; the fuel
        freeze lives here and is cleared here. Returns the one published frame.
        """
        self.initialize(airport)
        self.fuel_freeze = False
        self._frozen_fuel_lbs = None
        state = self._extract_and_publish()
        # one FlightGear frame too, even while frozen [a: D8]
        saved_frame, self._frame = self._frame, 0
        self._transmit_visual(state)
        self._frame = saved_frame
        return state

    def ssf_set_position(
        self, lat_deg: float, lon_deg: float, alt_m: float,
        speed_mps: float, heading_deg: float,
    ) -> None:
        """SET POSITION: Lat, Lon (deg), Altitude (m), Speed (m/s), Heading
        (deg). [a: L2SYS-REQ-01.10b]

        [a: D8] "SSF set-position airborne implies engine on and parking brake
        released."
        [a: D20, provisional] the Speed field is TRUE airspeed.
        """
        f = self._fdm
        terrain_ft = float(f["position/terrain-elevation-asl-ft"])
        alt_ft = alt_m * FT_PER_M
        airborne = (alt_ft - terrain_ft) > GROUND_START_AGL_FT

        f["ic/lat-gc-deg"] = lat_deg
        f["ic/long-gc-deg"] = lon_deg
        f["ic/terrain-elevation-ft"] = terrain_ft
        f["ic/h-sl-ft"] = alt_ft
        f["ic/psi-true-deg"] = heading_deg % 360.0
        f["ic/vt-fps"] = speed_mps / MPS_PER_FPS
        f["ic/gamma-deg"] = 0.0
        f.run_ic()
        f.set_dt(PHYSICS_DT)

        if airborne:
            # [a: D8]
            self.commands.update(engine_on=True, parking_brake=False,
                                 gear_down=False)
            f["propulsion/set-running"] = -1
            self._engine_commanded = True
            f["fcs/left-brake-cmd-norm"] = 0.0
            f["fcs/right-brake-cmd-norm"] = 0.0
        self._extract_and_publish()

    def ssf_fuel_freeze(self, engaged: bool) -> None:
        """FUEL FREEZE: hold tank contents constant. [a: L2SYS-REQ-01.10b]"""
        self.fuel_freeze = bool(engaged)
        if not engaged:
            self._frozen_fuel_lbs = None
        else:
            self._frozen_fuel_lbs = float(
                self._fdm["propulsion/total-fuel-lbs"]
            )

    def save_snapshot(self) -> Snapshot:
        """SAVE SNAPSHOT, physics side. [a: L2SYS-REQ-01.10b]"""
        s = self.telemetry.get() or self._extract_and_publish()
        return Snapshot(
            icao=self.airport.icao, lat_deg=s.lat_deg, lon_deg=s.lon_deg,
            alt_m=s.alt_m, heading_deg=s.heading_deg, pitch_deg=s.pitch_deg,
            roll_deg=s.roll_deg, speed_mps=s.speed_mps, fuel_lbs=s.fuel_lbs,
            engine_running=s.engine_running, parking_brake=s.parking_brake,
            air_brake=s.air_brake, gear_down=s.gear_down,
            fuel_freeze=self.fuel_freeze, sim_time_s=s.sim_time_s,
        )

    def load_snapshot(self, snap: Snapshot) -> OwnshipState:
        """LOAD SNAPSHOT, physics side. [a: L2SYS-REQ-01.10b]"""
        f = self._fdm
        f["ic/lat-gc-deg"] = snap.lat_deg
        f["ic/long-gc-deg"] = snap.lon_deg
        f["ic/h-sl-ft"] = snap.alt_m * FT_PER_M
        f["ic/psi-true-deg"] = snap.heading_deg
        f["ic/theta-deg"] = snap.pitch_deg
        f["ic/phi-deg"] = snap.roll_deg
        f["ic/vt-fps"] = snap.speed_mps / MPS_PER_FPS
        f.run_ic()
        f.set_dt(PHYSICS_DT)
        try:
            f.set_sim_time(snap.sim_time_s)
        except Exception:               # pragma: no cover - API variance
            pass
        self.commands.update(
            engine_on=snap.engine_running, parking_brake=snap.parking_brake,
            air_brake=snap.air_brake, gear_down=snap.gear_down,
        )
        self._engine_commanded = snap.engine_running
        if snap.engine_running:
            f["propulsion/set-running"] = -1
        self._restore_fuel(snap.fuel_lbs)
        # MEASURED: propulsion/total-fuel-lbs is an aggregate that JSBSim
        # recomputes during run(), not on a write to a tank. Writing the tanks
        # and reading the total in the same frame returns the PREVIOUS total
        # (observed: tanks set to 1485.485 + 1485.485, total still reading
        # 2882.57 until the next run(), then 2970.89). One frame is stepped
        # here so the published state carries the restored figure, and the
        # snapshot's sim time is reapplied afterwards so that frame does not
        # show up as a discrepancy. [a: D17]
        f.run()
        try:
            f.set_sim_time(snap.sim_time_s)
        except Exception:               # pragma: no cover - API variance
            pass
        self.fuel_freeze = snap.fuel_freeze
        self._frozen_fuel_lbs = snap.fuel_lbs if snap.fuel_freeze else None
        return self._extract_and_publish()

    def _restore_fuel(self, total_lbs: float) -> None:
        f = self._fdm
        current = float(f["propulsion/total-fuel-lbs"])
        if current <= 0.0:
            return
        scale = total_lbs / current
        for i in range(8):
            key = "propulsion/tank/contents-lbs" if i == 0 else f"propulsion/tank[{i}]/contents-lbs"
            try:
                value = float(f[key])
            except Exception:
                break
            if value:
                f[key] = value * scale

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------
    def run(self, stop_event: threading.Event | None = None,
            max_frames: int | None = None) -> int:
        """The 120 Hz loop. Stoppable. [a: D15] [a: L2SYS-REQ-01.02b]"""
        stop = stop_event or self.stop_event
        frames = 0
        while not stop.is_set():
            if max_frames is not None and frames >= max_frames:
                break
            started = self._clock()
            if self.running:
                self.step()
            frames += 1
            remaining = PHYSICS_DT - (self._clock() - started)
            if remaining > 0:
                self._sleep(remaining)
            elif not self.running:
                self._sleep(PHYSICS_DT)
        return frames

    def close(self) -> None:
        self.stop_event.set()
        if self._owns_socket and self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ------------------------------------------------------------------
    def drain_messages(self) -> list[str]:
        out, self.messages = self.messages, []
        return out


def _clip(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def parse_ground_elevation(payload: object) -> float | None:
    """Parse FlightGear's ground-elevation reply. [a: D4]

    "the value arriving as a number, a string, or wrapped in an object under
    `value`". Anything else yields None, which the poll treats as a failure and
    tolerates. [a: D9]
    """
    if isinstance(payload, dict):
        if "value" not in payload:
            return None
        payload = payload["value"]
    if isinstance(payload, bool):
        return None
    if isinstance(payload, (int, float)):
        v = float(payload)
        return v if math.isfinite(v) else None
    if isinstance(payload, str):
        try:
            v = float(payload.strip())
        except ValueError:
            return None
        return v if math.isfinite(v) else None
    return None
