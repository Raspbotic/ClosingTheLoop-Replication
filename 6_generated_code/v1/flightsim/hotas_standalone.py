"""
hotas_standalone.py
===================
HOTAS Control process.

TRACEABILITY
------------
[a] Implements part def HotasControl (fsim_TA_Peripherals.sysml), whose
    doc comment names this very file: "hotas_standalone.py".
[a] Requirement L2SYS-REQ-01.01b (fsim_Design_v4.xlsx): "The HOTAS shall
    continuously poll the physical joystick axes, throttle lever and
    buttons, and transmit the associated signals via UDP to the Virtual
    Ownship." Allocated logical block: Hotas (fsim_LA.sysml, R2F_L2
    matrix), realizing L2SYS-FCN-01.1, supporting req_manualFlight
    (fsim_Requirements.sysml) and UC_ManualFlight (fsim_UC.sysml).
[a] L2SYS-REQ-01.01 ("the system shall comprise a physical HOTAS") is
    V-Method I; this module is the software side of that item.
[a] Output port: e_hotasControlOut : UdpPort, portNumber = 5005
    (fsim_TA_Peripherals.sysml) -> ports.UDP_HOTAS_CONTROL [a: D4].
[a] Runtime: PythonRuntime version "3.11", rationale "pygame: no cp314
    wheels available" (fsim_TA_Peripherals.sysml) -> .venv_hotas [a: D5].
[a: legacy hotas_standalone.py] axis mapping roll=0, pitch=1, throttle=2,
    yaw candidates 3/4, and the debug-printout style, are taken verbatim
    from the attached prior-project file, which R-4 designates as the
    authority for both.
[a: Decision D7] "HOTAS yaw axis: pick the larger deflection of candidate
    axes 3/4 ('smart yaw', per legacy hardware)."
[a: Decision D15] the transmit loop accepts an optional stop_event so no
    test leaks input into a later test.

TA ACTION -> CODE MAPPING [b]
-----------------------------
    measureStickDeflection   -> _read_axes() roll/pitch
    measureThrottlePosition  -> _read_axes() throttle
    measureYawTwist          -> smart_yaw() over the candidate axes
    readSwitches             -> _read_switches()
    formatControlPayload     -> HotasControls.to_json()
    transmitControlData      -> run() sock.sendto()

DEVELOPER CHOICES [c] (not in the design specification)
--------------------------------------------------------
- pygame as joystick backend (SDL2; robust HOTAS support on Windows).
- Poll/tx rate 60 Hz: "continuously" is unquantified in 01.01b; 60 Hz
  halves the 120 Hz physics rate and is far above human input bandwidth.
  Adjustable via --rate.
- Throttle axes report +1..-1 on most hardware; normalized here to 0..1.
- --demo mode synthesizes a gentle stick pattern so integration tests can
  run without physical hardware.
- --debug-axes: raw, unmapped axis dump for empirically identifying
  indices on hardware that doesn't match the defaults -- no guessing, no
  UDP transmission, just numbers to read off the console.

Usage:
    python hotas_standalone.py                 # first joystick, defaults
    python hotas_standalone.py --list          # enumerate devices
    python hotas_standalone.py --debug-axes    # raw axis id calibration
    python hotas_standalone.py --demo --debug  # no hardware, live readout
    python hotas_standalone.py --yaw-axes 4    # rudder confirmed on axis 4
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
import time

from flightsim.common import ports
from flightsim.common.messages import HotasControls


# --------------------------------------------------------------------------
# Axis-role helpers
# --------------------------------------------------------------------------

def smart_yaw(candidate_values) -> float:
    """Pick the candidate yaw axis with the largest deflection.

    [a: D7] "HOTAS yaw axis: pick the larger deflection of candidate axes
    3/4 ('smart yaw', per legacy hardware)."
    [a: legacy hotas_standalone.py, "Smart Rudder Detection"] the rudder /
    twist control's axis index is device-dependent, so both candidates are
    read every frame and the larger magnitude wins.

    Pure function so the rule is verifiable without hardware.
    """
    yaw = 0.0
    best_mag = 0.0
    for v in candidate_values:
        v = float(v)
        if abs(v) > best_mag:
            best_mag, yaw = abs(v), v
    return yaw


def normalize_throttle(raw_axis_value: float) -> float:
    """[c] Most HOTAS throttle axes report +1 (idle) .. -1 (max); map to
    0..1 as required by the D1 wire range."""
    return max(0.0, min(1.0, (1.0 - float(raw_axis_value)) / 2.0))


# --------------------------------------------------------------------------
# Hardware backend (pygame) -- imported lazily so --demo works without it
# --------------------------------------------------------------------------

class JoystickSource:
    """Wraps one pygame joystick. [c] backend choice.

    Axis roles [a: legacy hotas_standalone.py, field-verified mapping]:
        roll_axis=0, pitch_axis=1, throttle_axis=2 : single index each
        yaw_axes=[3, 4] : candidate indices resolved by smart_yaw() [a: D7]
    """

    def __init__(self, index: int = 0, roll_axis: int = 0, pitch_axis: int = 1,
                 throttle_axis: int = 2, yaw_axes=None,
                 deadzone: float = 0.05):
        import pygame  # local import: --demo/--list must not require it
        pygame.init()
        pygame.joystick.init()
        self._pygame = pygame
        self._index = index
        self._last_rescan = 0.0
        # [a] R-4: "hot-plug tolerance -- start without a device and wait".
        if pygame.joystick.get_count() == 0:
            print("[hotas] no joystick detected -- waiting for device "
                  "(virtual HOTAS active on the PCS meanwhile)...")
            self._js = None
            self._connected = False
        else:
            self._js = pygame.joystick.Joystick(index)
            self._js.init()
            self._connected = True
        self._roll_axis = roll_axis
        self._pitch_axis = pitch_axis
        self._throttle_axis = throttle_axis
        self._yaw_axes = list(yaw_axes) if yaw_axes else [3, 4]
        self._deadzone = deadzone
        if self._js is not None:
            print(f"[hotas] using device {index}: {self._js.get_name()!r}, "
                  f"{self._js.get_numaxes()} axes, "
                  f"{self._js.get_numbuttons()} buttons")
        print(f"[hotas] axis roles: roll={roll_axis} pitch={pitch_axis} "
              f"throttle={throttle_axis} yaw_candidates={self._yaw_axes} "
              f"-- run --debug-axes if these are wrong for your hardware")

    # -- raw access ------------------------------------------------------
    def _raw_axis(self, idx: int) -> float:
        if self._js is None or idx >= self._js.get_numaxes():
            return 0.0
        return self._js.get_axis(idx)

    def _axis(self, idx: int) -> float:
        v = self._raw_axis(idx)
        return 0.0 if abs(v) < self._deadzone else max(-1.0, min(1.0, v))

    def raw_axes(self) -> list:
        """All raw, unmapped axis values -- for --debug-axes calibration."""
        self._pygame.event.pump()
        return [self._js.get_axis(i) for i in range(self._js.get_numaxes())]

    # -- hot-plug --------------------------------------------------------
    def _service_hotplug(self) -> bool:
        """Returns True while a device is connected.

        [a] R-4: "suspend transmission on JOYDEVICEREMOVED (so the PCS falls
        back to virtual controls), resume on reconnect with a 1 Hz
        joystick-subsystem rescan fallback."
        [b] Transmitting stale values after a USB disconnect would keep
        'physical precedence' alive on the PCS [a: D10] and block the
        virtual HOTAS from engaging -- hence full silence, not a freeze.
        """
        pg = self._pygame
        for ev in pg.event.get():
            if ev.type == getattr(pg, "JOYDEVICEREMOVED", None):
                if self._connected:
                    print("\n[hotas] DEVICE REMOVED -- transmission "
                          "suspended (virtual HOTAS takes over on the PCS)")
                self._connected = False
            elif ev.type == getattr(pg, "JOYDEVICEADDED", None):
                try:
                    self._js = pg.joystick.Joystick(0)
                    self._js.init()
                    self._connected = True
                    print("\n[hotas] device reconnected: "
                          f"{self._js.get_name()!r} -- transmission resumed")
                except Exception:
                    self._connected = False
        if self._connected:
            return True
        # [b] fallback rescan every 1 s: some platforms deliver no
        # JOYDEVICEADDED event without a window; re-init the joystick
        # subsystem to force SDL to re-enumerate devices.
        now = time.monotonic()
        if now - self._last_rescan > 1.0:
            self._last_rescan = now
            pg.joystick.quit()
            pg.joystick.init()
            if pg.joystick.get_count() > 0:
                try:
                    self._js = pg.joystick.Joystick(
                        min(self._index, pg.joystick.get_count() - 1))
                    self._js.init()
                    self._connected = True
                    print(f"\n[hotas] device detected: "
                          f"{self._js.get_name()!r} -- transmitting")
                except Exception:
                    self._connected = False
        return self._connected

    # -- one frame -------------------------------------------------------
    def read(self):
        """Returns HotasControls, or None while the device is unplugged."""
        if not self._service_hotplug():
            return None
        self._pygame.event.pump()
        roll = self._axis(self._roll_axis)            # measureStickDeflection
        pitch = self._axis(self._pitch_axis)          # measureStickDeflection
        throttle = normalize_throttle(               # measureThrottlePosition
            self._axis(self._throttle_axis))
        yaw = smart_yaw(self._axis(i) for i in self._yaw_axes)  # measureYawTwist
        switches = {                                  # readSwitches
            f"btn{i}": bool(self._js.get_button(i))
            for i in range(self._js.get_numbuttons())
        }
        return HotasControls(roll=roll, pitch=pitch, yaw=yaw,
                             throttle=throttle, switches=switches)


class DemoSource:
    """[c] Synthetic input pattern for hardware-free integration testing.

    Used by the R-9 end-to-end test and by --demo.
    """

    def __init__(self):
        self._t0 = time.monotonic()
        print("[hotas] DEMO mode: synthesizing gentle control inputs")

    def read(self) -> HotasControls:
        t = time.monotonic() - self._t0
        return HotasControls(
            roll=0.2 * math.sin(0.20 * t),
            pitch=0.1 * math.sin(0.13 * t),
            yaw=0.0,
            throttle=0.75,
            switches={"btn0": False},
        )


# --------------------------------------------------------------------------
# Calibration tool: raw, unmapped axis dump [c]
# --------------------------------------------------------------------------

def raw_axis_dump(device_index: int, refresh_hz: float = 10.0) -> None:
    """Print every raw axis value continuously. No mapping, no UDP tx.

    Move one physical control at a time and read which index changes --
    this is how --roll-axis/--pitch-axis/--throttle-axis/--yaw-axes are
    determined for hardware that doesn't match the shipped defaults.
    """
    import pygame
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        sys.exit("No joystick detected.")
    js = pygame.joystick.Joystick(device_index)
    js.init()
    n_axes = js.get_numaxes()
    n_buttons = js.get_numbuttons()
    print(f"[hotas] --debug-axes: {js.get_name()!r}, {n_axes} axes, "
          f"{n_buttons} buttons. Move ONE control at a time. Ctrl+C to stop.")
    period = 1.0 / refresh_hz
    prev_buttons = [False] * n_buttons
    try:
        while True:
            pygame.event.pump()
            axes = [js.get_axis(i) for i in range(n_axes)]
            line = "  ".join(f"ax{i}={v:+.2f}" for i, v in enumerate(axes))
            print(f"\r[hotas] {line}   ", end="", flush=True)
            for i in range(n_buttons):
                pressed = bool(js.get_button(i))
                if pressed and not prev_buttons[i]:
                    print(f"\n[hotas] button DOWN : btn{i}")
                prev_buttons[i] = pressed
            time.sleep(period)
    except KeyboardInterrupt:
        print("\n[hotas] stopped.")


# --------------------------------------------------------------------------
# Debug console readout [c] -- the legacy file's connect banner + one line
# per button edge, generalized to all axes/switches.
# --------------------------------------------------------------------------

class DebugPrinter:
    """Throttled in-place status line + edge-triggered button events."""

    def __init__(self, refresh_hz: float = 5.0):
        self._period = 1.0 / refresh_hz
        self._t_next = 0.0
        self._prev_switches = {}

    def update(self, msg: HotasControls) -> None:
        # Edge events -- never throttled; mirrors the legacy file's
        # "[CMD] Airbrake Toggle Sent".
        for name, pressed in msg.switches.items():
            was = self._prev_switches.get(name, False)
            if pressed and not was:
                print(f"\n[hotas] button DOWN : {name}")
            elif was and not pressed:
                print(f"\n[hotas] button UP   : {name}")
        self._prev_switches = dict(msg.switches)

        now = time.monotonic()
        if now < self._t_next:
            return
        self._t_next = now + self._period
        print(f"\r[hotas] seq={msg.seq:6d}  "
              f"roll={msg.roll:+.2f}  pitch={msg.pitch:+.2f}  "
              f"yaw={msg.yaw:+.2f}  throttle={msg.throttle:.2f}   ",
              end="", flush=True)


# --------------------------------------------------------------------------
# Transmit loop  [a] L2SYS-REQ-01.01b, TA action transmitControlData
# --------------------------------------------------------------------------

def run(source, rate_hz: float = ports.HOTAS_RATE_HZ,
        host: str = ports.LOCALHOST, port: int = ports.UDP_HOTAS_CONTROL,
        debug: bool = False, stop_event=None, sock=None) -> None:
    """Poll the source and transmit continuously at rate_hz.

    [a] L2SYS-REQ-01.01b "continuously poll ... and transmit ... via UDP".
    [a: D4] default destination 127.0.0.1:5005.
    [a: D15] stop_event makes the loop stoppable from a test teardown.
    """
    own_sock = sock is None
    sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period = 1.0 / float(rate_hz)
    seq = 0
    printer = DebugPrinter() if debug else None
    print(f"[hotas] transmitting to udp://{host}:{port} at {rate_hz:.0f} Hz")
    try:
        while stop_event is None or not stop_event.is_set():
            t_next = time.monotonic() + period
            msg = source.read()                      # measure* / readSwitches
            if msg is None:                          # device unplugged:
                time.sleep(period)                   # stay silent so the PCS
                continue                             # falls back [a: D10]
            msg.seq = seq
            sock.sendto(msg.to_json(), (host, port))  # formatControlPayload +
            seq += 1                                  # transmitControlData
            if printer is not None:
                printer.update(msg)
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\n[hotas] stopped.")
    finally:
        if own_sock:
            try:
                sock.close()
            except OSError:                          # [a: D3] teardown race
                pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Flight Simulator HOTAS process "
                                            "(TA: HotasControl)")
    p.add_argument("--list", action="store_true",
                   help="list joystick devices and exit")
    p.add_argument("--device", type=int, default=0,
                   help="joystick index (default 0)")
    p.add_argument("--roll-axis", type=int, default=0,
                   help="axis index for roll (default 0) [a: legacy file]")
    p.add_argument("--pitch-axis", type=int, default=1,
                   help="axis index for pitch (default 1) [a: legacy file]")
    p.add_argument("--throttle-axis", type=int, default=2,
                   help="axis index for throttle (default 2) [a: legacy file]")
    p.add_argument("--yaw-axes", type=int, nargs="+", default=[3, 4],
                   metavar="AXIS",
                   help="candidate axis indices for yaw/rudder; the one with "
                        "the larger deflection wins each frame (default: 3 4) "
                        "[a: D7 smart yaw / legacy 'Smart Rudder Detection']")
    p.add_argument("--deadzone", type=float, default=0.05,
                   help="axis deadzone (default 0.05) [c]")
    p.add_argument("--rate", type=float, default=ports.HOTAS_RATE_HZ,
                   help="poll/tx rate in Hz (default 60) [c]")
    p.add_argument("--host", default=ports.LOCALHOST,
                   help="PCS host (default 127.0.0.1) [a: TA address]")
    p.add_argument("--port", type=int, default=ports.UDP_HOTAS_CONTROL,
                   help="PCS control port (default 5005) [a: D4/TA portNumber]")
    p.add_argument("--demo", action="store_true",
                   help="synthesize inputs; no hardware needed [c]")
    p.add_argument("--debug", action="store_true",
                   help="print live axis/button readout to console [c]")
    p.add_argument("--debug-axes", action="store_true",
                   help="raw unmapped axis dump for calibration; no tx [c]")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)

    if args.list:
        import pygame
        pygame.init()
        pygame.joystick.init()
        n = pygame.joystick.get_count()
        print(f"{n} joystick(s) found:")
        for i in range(n):
            js = pygame.joystick.Joystick(i)
            js.init()
            print(f"  [{i}] {js.get_name()} "
                  f"({js.get_numaxes()} axes, {js.get_numbuttons()} buttons)")
        return

    if args.debug_axes:
        raw_axis_dump(args.device)
        return

    source = DemoSource() if args.demo else JoystickSource(
        args.device, args.roll_axis, args.pitch_axis, args.throttle_axis,
        args.yaw_axes, args.deadzone)
    run(source, args.rate, args.host, args.port, debug=args.debug)


if __name__ == "__main__":
    main()
