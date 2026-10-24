"""ThreadPhysics — Virtual Ownship. [a: fsim_TA_PCS_ThreadPhysics.sysml#ThreadPhysics]

initializationPhase (initFDM, setInitialConditions, updateEnvironment) and the 120 Hz
cyclicExecutionLoop (acquireControlInputs, mapInputsToFDM, executePhysicsStep,
extractTelemetry, trasmitTelemetrySignal, trasmitVisualTelemetry).
[a: L2SYS-REQ-01.02] [a: L2SYS-REQ-01.02b]

Ports: i_HotasControlIn = `commands` queue; i_OwnTelemetryOut = `latest()` / `telemetry`
queue; e_nativeJSBSimFdmOut = UDP 5550.
External-library conventions are measured (tools/measure_jsbsim.py, docs/MEASUREMENTS.md)
and reconciled in `_map_inputs` only.
"""
from __future__ import annotations

import copy
import math
import queue
import socket
import threading
import time

import jsbsim

from flightsim.common import ports
from flightsim.pcs import autopilot as ap_law
from flightsim.pcs import fgnetfdm, gpws
from flightsim.pcs.airfields import load_airfields, DEFAULT_ICAO

FT = 0.3048
KT = 0.514444
FDM_MODEL = "f16"  # [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#ssfLaunchOtwView "aircraft type (e.g. f16)"]
IDLE_GATE = 0.10   # [a: Behaviour B6] engine start refused above 10 % throttle
MIL_DETENT = 0.85  # [c] wire throttle at MIL power; above it = afterburner. Settled by the HOTAS detent.


def throttle_to_fdm(t):
    """Wire throttle (0 idle .. 1 full afterburner) -> JSBSim f16 fcs/throttle-cmd-norm.
    [b: measured M13] the f16 FCS doubles the command: cmd 0.5 = MIL (N2 100 %), cmd > 0.5 =
    afterburner. A linear 1:1 mapping put MIL at mid-travel (field defect D-1: taxi too fast)."""
    t = max(0.0, min(1.0, t))
    if t <= MIL_DETENT:
        return 0.5 * t / MIL_DETENT
    return 0.5 + 0.5 * (t - MIL_DETENT) / (1.0 - MIL_DETENT)
N2_IDLE = 50.0     # [b: measured idle N2 53 %, docs/MEASUREMENTS.md M3] starter released above this
N2_LIGHT = 15.0    # [b: measured M3] crank to 15 % N2, then cutoff off
TERRAIN_SLEW_FT = 1.0  # [a: Behaviour B7] per physics frame
SPAWN_ALONG_M = 30.0   # [c] ground start 30 m past the threshold, on the pavement
GROUND_AGL_M = 5.0     # [c] set-position at or below 5 m AGL is a ground position


class ThreadPhysics:
    def __init__(self, airfields=None, icao=DEFAULT_ICAO, clock=time.monotonic,
                 fdm_host=ports.LOOPBACK, fdm_port=ports.NATIVE_FDM_PORT, send_fdm=True):
        self.dt = 1.0 / ports.PHYSICS_RATE_HZ  # [a: L2SYS-REQ-01.02b]
        self.decim = int(round(ports.PHYSICS_RATE_HZ / ports.NATIVE_FDM_RATE_HZ))  # [a: Behaviour B13]
        self.clock = clock
        self.airfields = airfields or load_airfields(None)
        self.icao = icao
        self.commands = queue.Queue()      # i_HotasControlIn
        self.messages = queue.Queue()      # text for the message window
        self._latest = None
        self._lock = threading.Lock()
        self.step_count = 0
        self.frame_count = 0
        self.fdm_sent = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if send_fdm else None
        self._fdm_addr = (fdm_host, fdm_port)
        self._stop = threading.Event()
        self._thread = None
        self.initialization_phase()

    # ------------------------------------------------------------------ init
    def initialization_phase(self, snapshot=None):
        """initFDM + setInitialConditions + updateEnvironment."""
        self._init_fdm()
        self.running = False  # starts frozen [c]: airport selectable before RUN (01.04b)
        self.sim_time = 0.0
        self.controls = dict(roll=0.0, pitch=0.0, yaw=0.0, throttle=0.0, wheel_brake=False)
        self.park_brake = True    # [a: Behaviour B6]
        self.air_brake = False
        self.gear_down = True     # [a: Behaviour B6]
        self.engine_state = "OFF"  # [a: Behaviour B6] [a: L2SYS-REQ-01.07]
        self.fuel_freeze = False
        self.ap = dict(ap=False, ah=False, at=False, hdg=0.0, alt=0.0, spd=0.0)
        self.terrain_target_ft = None
        self._set_ground_ic(self.airfields[self.icao])
        if snapshot is not None:
            self._restore(snapshot)
        self._publish(force_fdm=True)

    def _init_fdm(self):
        fdm = jsbsim.FGFDMExec(None)
        fdm.set_debug_level(0)
        fdm.load_model(FDM_MODEL)
        fdm.set_dt(self.dt)
        self.fdm = fdm

    def _set_ground_ic(self, af):
        f = self.fdm
        hdg = math.radians(af.runway_hdg_deg)
        dlat = SPAWN_ALONG_M * math.cos(hdg) / 111320.0
        dlon = SPAWN_ALONG_M * math.sin(hdg) / (111320.0 * math.cos(math.radians(af.lat_deg)))
        f["ic/lat-geod-deg"] = af.lat_deg + dlat
        f["ic/long-gc-deg"] = af.lon_deg + dlon
        f["ic/terrain-elevation-ft"] = af.elevation_m / FT
        f["ic/h-agl-ft"] = 0.0
        f["ic/psi-true-deg"] = af.runway_hdg_deg
        f["ic/vc-kts"] = 0.0
        f.run_ic()
        f["position/terrain-elevation-asl-ft"] = af.elevation_m / FT
        self._engine_off()
        f["gear/gear-cmd-norm"] = 1.0
        f["fcs/speedbrake-cmd-norm"] = 0.0
        self._map_inputs()
        self._settle()

    def _settle(self):
        """Let gear compression settle without advancing simulation time. [c]"""
        for _ in range(240):
            self.fdm.run()

    # ---------------------------------------------------------------- engine
    def _engine_off(self):
        self.fdm["propulsion/cutoff_cmd"] = 1  # [b: measured M1: underscore name exists]
        self.fdm["propulsion/starter_cmd"] = 0
        self.engine_state = "OFF"

    def _engine_start(self):
        if self.controls["throttle"] > IDLE_GATE:
            self.messages.put("ENGINE START REFUSED: THROTTLE ABOVE IDLE")  # [a: Behaviour B6]
            return False
        # [b: measured M3] the starter cranks only with cutoff set; fuel is admitted
        # ("running from cutoff" [a: Behaviour B6]) once N2 reaches N2_LIGHT, see step().
        self.fdm["propulsion/cutoff_cmd"] = 1
        self.fdm["propulsion/starter_cmd"] = 1
        self.engine_state = "STARTING"
        self.messages.put("ENGINE START: STARTER ENGAGED, SPOOLING TO IDLE")
        return True

    def _engine_running_now(self):
        """Airborne set-position implies engine ON. [a: Behaviour B6]"""
        self.fdm["propulsion/cutoff_cmd"] = 0
        self.fdm["propulsion/starter_cmd"] = 0
        self.fdm["propulsion/set-running"] = -1  # [b: measured M3: immediate idle]
        self.engine_state = "RUNNING"

    # -------------------------------------------------------------- commands
    def _handle(self, cmd, arg):
        frozen_only = {"reset", "set_position", "select_airport", "load_snapshot", "save_snapshot"}
        if cmd in frozen_only and self.running:
            # [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#manageSSF caveats] [a: L2SYS-REQ-01.04b]
            self.messages.put(f"{cmd.upper()} REFUSED: FLIGHT-FREEZE REQUIRED")
            if cmd == "save_snapshot":
                arg.put(None)
            return
        if cmd == "controls":
            self.controls.update(arg)
        elif cmd == "engine":
            if arg and self.engine_state == "OFF":
                self._engine_start()
            elif not arg:
                self._engine_off()
        elif cmd == "park_brake":
            self.park_brake = bool(arg)
        elif cmd == "air_brake":
            self.air_brake = bool(arg)
        elif cmd == "gear":
            if not arg and self._wow():
                self.messages.put("GEAR UP REFUSED: WEIGHT ON WHEELS")  # [c] measured: FDM has no interlock (M7)
            else:
                self.gear_down = bool(arg)
        elif cmd == "run":
            self.running = True
        elif cmd == "pause":
            self.running = False
        elif cmd == "reset":
            self.initialization_phase()   # clean slate at selected airfield [a: Behaviour B6]
            self.messages.put(f"RESET: GROUND START AT {self.icao}")
        elif cmd == "select_airport":
            if arg in self.airfields:
                self.icao = arg
                self.initialization_phase()
                self.messages.put(f"AIRPORT SELECTED: {arg}")
        elif cmd == "set_position":
            self._set_position(**arg)
        elif cmd == "fuel_freeze":
            self.fuel_freeze = bool(arg)
            self.fdm["propulsion/fuel_freeze"] = 1 if arg else 0  # [b: measured M8]
        elif cmd == "autopilot":
            self._autopilot(arg)
        elif cmd == "terrain":
            self.terrain_target_ft = arg / FT  # [a: Behaviour B7]
        elif cmd == "save_snapshot":
            arg.put(self._snapshot())
        elif cmd == "load_snapshot":
            self.initialization_phase(snapshot=arg)
            self.messages.put("SNAPSHOT LOADED")

    def _autopilot(self, arg):
        """Sync-on-engage: engaging captures the current value. [a: Behaviour B9]"""
        t = self._telemetry_raw()
        for mode, key, cur in (("ap", "hdg", t["heading_deg"]), ("ah", "alt", t["alt_m"]), ("at", "spd", t["speed_mps"])):
            if mode in arg:
                if arg[mode] and not self.ap[mode]:
                    self.ap[key] = cur
                self.ap[mode] = bool(arg[mode])
            if key in arg and arg[key] is not None:
                self.ap[key] = float(arg[key])

    def _set_position(self, lat_deg, lon_deg, alt_m, speed_mps, heading_deg):
        """[a: L2SYS-REQ-01.10b] SET POSITION: Lat, Lon (deg), Altitude (m), Speed (m/s), Heading (deg)."""
        f = self.fdm
        terrain_ft = f["position/terrain-elevation-asl-ft"]
        airborne = alt_m - terrain_ft * FT > GROUND_AGL_M
        f["ic/lat-geod-deg"] = lat_deg
        f["ic/long-gc-deg"] = lon_deg
        f["ic/terrain-elevation-ft"] = terrain_ft
        f["ic/psi-true-deg"] = heading_deg
        if airborne:
            f["ic/h-sl-ft"] = alt_m / FT
            f["ic/vt-fps"] = speed_mps / FT
        else:
            f["ic/h-agl-ft"] = 0.0
            f["ic/vt-fps"] = 0.0
        f["ic/theta-deg"] = 0.0
        f["ic/phi-deg"] = 0.0
        f.run_ic()
        if airborne:
            self._engine_running_now()   # [a: Behaviour B6]
            self.park_brake = False      # [a: Behaviour B6]
        self._map_inputs()
        self._publish(force_fdm=True)
        self.messages.put(f"POSITION SET: {lat_deg:.4f} {lon_deg:.4f} {alt_m:.0f} m")

    # -------------------------------------------------------------- snapshot
    _SNAP_PROPS = ("position/lat-geod-deg", "position/long-gc-deg", "position/h-sl-ft",
                   "position/terrain-elevation-asl-ft", "velocities/u-fps", "velocities/v-fps",
                   "velocities/w-fps", "velocities/p-rad_sec", "velocities/q-rad_sec",
                   "velocities/r-rad_sec", "attitude/phi-deg", "attitude/theta-deg", "attitude/psi-deg",
                   "propulsion/tank/contents-lbs", "propulsion/tank[1]/contents-lbs",
                   "propulsion/tank[2]/contents-lbs", "propulsion/engine/n2", "gear/gear-pos-norm")

    def _snapshot(self):
        props = {p: float(self.fdm[p]) for p in self._SNAP_PROPS}
        return copy.deepcopy(dict(props=props, icao=self.icao, sim_time=self.sim_time,
                                  controls=self.controls, park_brake=self.park_brake,
                                  air_brake=self.air_brake, gear_down=self.gear_down,
                                  engine_state=self.engine_state, fuel_freeze=self.fuel_freeze,
                                  ap=self.ap, wow=self._wow()))

    def _restore(self, snap):
        snap = copy.deepcopy(snap)  # no aliasing [a: Behaviour B12]
        p = snap["props"]
        f = self.fdm
        self.icao = snap["icao"]
        f["ic/lat-geod-deg"] = p["position/lat-geod-deg"]
        f["ic/long-gc-deg"] = p["position/long-gc-deg"]
        f["ic/terrain-elevation-ft"] = p["position/terrain-elevation-asl-ft"]
        if snap.get("wow"):
            f["ic/h-agl-ft"] = 0.0
        else:
            f["ic/h-sl-ft"] = p["position/h-sl-ft"]
        for k, ick in (("velocities/u-fps", "ic/u-fps"), ("velocities/v-fps", "ic/v-fps"), ("velocities/w-fps", "ic/w-fps"),
                       ("velocities/p-rad_sec", "ic/p-rad_sec"), ("velocities/q-rad_sec", "ic/q-rad_sec"),
                       ("velocities/r-rad_sec", "ic/r-rad_sec"), ("attitude/phi-deg", "ic/phi-deg"),
                       ("attitude/theta-deg", "ic/theta-deg"), ("attitude/psi-deg", "ic/psi-true-deg")):
            f[ick] = p[k]
        f.run_ic()
        f["position/terrain-elevation-asl-ft"] = p["position/terrain-elevation-asl-ft"]
        for i, key in enumerate(("propulsion/tank/contents-lbs", "propulsion/tank[1]/contents-lbs", "propulsion/tank[2]/contents-lbs")):
            f[key] = p[key]
        self.sim_time = snap["sim_time"]
        self.controls = snap["controls"]
        self.park_brake, self.air_brake, self.gear_down = snap["park_brake"], snap["air_brake"], snap["gear_down"]
        self.ap = snap["ap"]
        if snap["engine_state"] in ("RUNNING", "STARTING"):
            self._engine_running_now()
        else:
            self._engine_off()
        self.fuel_freeze = snap["fuel_freeze"]
        f["propulsion/fuel_freeze"] = 1 if self.fuel_freeze else 0
        f["gear/gear-pos-norm"] = p["gear/gear-pos-norm"]
        self._map_inputs()
        if snap.get("wow"):
            self._settle()

    # ------------------------------------------------------------ cyclic loop
    def _wow(self):
        return bool(self.fdm["gear/unit[0]/WOW"]) or bool(self.fdm["gear/unit[1]/WOW"])

    def _effective_controls(self):
        """AP outputs override the stick channels while engaged. [a: Behaviour B9]"""
        c = dict(self.controls)
        f = self.fdm
        if self.ap["ah"]:
            c["pitch"] = ap_law.altitude_hold(self.ap["alt"], f["position/h-sl-meters"], f["velocities/h-dot-fps"] * FT, f["attitude/theta-deg"])
        if self.ap["ap"]:
            c["roll"] = ap_law.heading_hold(self.ap["hdg"], f["attitude/psi-deg"], f["attitude/phi-deg"])
        if self.ap["at"]:
            c["throttle"] = ap_law.autothrottle(self.ap["spd"], f["velocities/vtrue-fps"] * FT)
        return c

    def _map_inputs(self):
        """mapInputsToFDM — the one reconciliation point between wire and JSBSim conventions.
        Wire: roll +right, pitch +stick back [a: Behaviour B8], yaw +right pedal.
        JSBSim f16 (measured, docs/MEASUREMENTS.md M4/M5): aileron +  -> roll right;
        elevator + -> nose down; rudder + -> nose left; steer + -> turn right."""
        c = self._effective_controls()
        self._eff = c
        f = self.fdm
        f["fcs/aileron-cmd-norm"] = c["roll"]
        f["fcs/elevator-cmd-norm"] = -c["pitch"]
        f["fcs/rudder-cmd-norm"] = -c["yaw"]   # yaw drives rudder ... [a: Behaviour B5]
        f["fcs/steer-cmd-norm"] = c["yaw"]     # ... and nose-wheel steering [a: Behaviour B5]
        f["fcs/throttle-cmd-norm"] = throttle_to_fdm(c["throttle"])
        brake = 1.0 if (self.park_brake or c.get("wheel_brake")) else 0.0  # [a: L2SYS-REQ-01.06c] [a: Behaviour B5]
        for p in ("fcs/left-brake-cmd-norm", "fcs/right-brake-cmd-norm", "fcs/center-brake-cmd-norm"):
            f[p] = brake
        f["fcs/speedbrake-cmd-norm"] = 1.0 if self.air_brake else 0.0  # [a: L2SYS-REQ-01.06d]
        f["gear/gear-cmd-norm"] = 1.0 if self.gear_down else 0.0        # [a: L2SYS-REQ-01.06e]

    def _update_environment(self):
        """Terrain slew ≤ 1 ft per frame toward the visual system's elevation. [a: Behaviour B7]"""
        if self.terrain_target_ft is None:
            return
        cur = self.fdm["position/terrain-elevation-asl-ft"]
        d = self.terrain_target_ft - cur
        if abs(d) > 1e-6:
            self.fdm["position/terrain-elevation-asl-ft"] = cur + max(-TERRAIN_SLEW_FT, min(TERRAIN_SLEW_FT, d))

    def step(self):
        """One 120 Hz cycle. Returns True if the FDM advanced."""
        self.step_count += 1
        handled = False
        while True:  # acquireControlInputs
            try:
                cmd, arg = self.commands.get_nowait()
            except queue.Empty:
                break
            self._handle(cmd, arg)
            handled = True
        if not self.running:
            if handled:
                self._map_inputs()
                self._publish(send_fdm=False)  # state changes visible while frozen [c]
            return False
        self._map_inputs()
        self._update_environment()
        self.fdm.run()  # executePhysicsStep
        self.sim_time += self.dt
        self.frame_count += 1
        if self.engine_state == "STARTING":
            n2 = self.fdm["propulsion/engine/n2"]
            if n2 >= N2_LIGHT and self.fdm["propulsion/cutoff_cmd"]:
                self.fdm["propulsion/cutoff_cmd"] = 0
            # [b: measured M3] release the starter only once the FDM reports the engine running
            if n2 >= N2_IDLE and self.fdm["propulsion/engine/set-running"] >= 1:
                self.fdm["propulsion/starter_cmd"] = 0
                self.engine_state = "RUNNING"
                self.messages.put("ENGINE RUNNING AT IDLE")
        self._publish()
        return True

    def _telemetry_raw(self):
        f = self.fdm
        return dict(
            sim_time_s=self.sim_time, model=FDM_MODEL,
            lat_deg=f["position/lat-geod-deg"], lon_deg=f["position/long-gc-deg"],
            alt_m=f["position/h-sl-meters"], agl_m=f["position/h-agl-ft"] * FT,
            v_speed_mps=f["velocities/h-dot-fps"] * FT, speed_mps=f["velocities/vtrue-fps"] * FT,
            groundspeed_mps=f["velocities/vg-fps"] * FT, vcas_kts=f["velocities/vc-kts"],
            heading_deg=f["attitude/psi-deg"] % 360.0, pitch_deg=f["attitude/theta-deg"],
            roll_deg=f["attitude/phi-deg"], pitch_rate_dps=math.degrees(f["velocities/q-rad_sec"]),
            alpha_deg=f["aero/alpha-deg"], beta_deg=f["aero/beta-deg"],
        )

    def _publish(self, force_fdm=False, send_fdm=True):
        """extractTelemetry + trasmitTelemetrySignal + trasmitVisualTelemetry."""
        f = self.fdm
        t = self._telemetry_raw()
        eff = getattr(self, "_eff", self.controls)
        wow = self._wow()
        t.update(
            n2_pct=f["propulsion/engine/n2"], fuel_lbs=f["propulsion/total-fuel-lbs"],
            engine_state=self.engine_state, park_brake=self.park_brake, air_brake=self.air_brake,
            speedbrake_pos=f["fcs/speedbrake-pos-norm"], gear_down=self.gear_down,
            gear_pos=f["gear/gear-pos-norm"], wow=wow, steer_pos=f["fcs/steer-pos-deg"] / 90.0,
            aileron_cmd=eff["roll"], elevator_cmd=-eff["pitch"], rudder_cmd=-eff["yaw"],
            throttle_cmd=eff["throttle"], gear_cmd=1.0 if self.gear_down else 0.0,
            trim_pitch=0.0, trim_roll=0.0, trim_yaw=0.0,
            ap_engaged=self.ap["ap"], ah_engaged=self.ap["ah"], at_engaged=self.ap["at"],
            target_heading_deg=self.ap["hdg"], target_alt_m=self.ap["alt"], target_speed_mps=self.ap["spd"],
            fuel_freeze=self.fuel_freeze, running=self.running, airport=self.icao,
            terrain_ft=f["position/terrain-elevation-asl-ft"],
        )
        # [a: Behaviour B15] evaluated every physics frame
        t["warning_state"] = gpws.warning(t["agl_m"], t["v_speed_mps"], wow, self.park_brake)
        with self._lock:
            self._latest = t
        if force_fdm or (send_fdm and self.frame_count % self.decim == 0):
            self._send_fdm(t)

    def _send_fdm(self, t):
        if self._sock is None:
            return
        try:
            self._sock.sendto(fgnetfdm.pack(t), self._fdm_addr)
            self.fdm_sent += 1
        except OSError:
            pass

    def latest(self):
        """i_OwnTelemetryOut: copy of the most recent telemetry."""
        with self._lock:
            return dict(self._latest) if self._latest else None

    # ---------------------------------------------------------------- thread
    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ThreadPhysics", daemon=False)
        self._thread.start()

    def _run(self):
        nxt = self.clock()
        while not self._stop.is_set():
            self.step()
            nxt += self.dt
            delay = nxt - self.clock()
            if delay > 0:
                self._stop.wait(delay)
            elif delay < -0.25:
                nxt = self.clock()  # fell behind: resynchronize [c]

    def stop_thread(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2.0)
            self._thread = None

    def stop(self):
        self.stop_thread()
        if self._sock:
            self._sock.close()
            self._sock = None

    def send(self, cmd, arg=None):
        self.commands.put((cmd, arg))
