"""
hotas_standalone.py
===================
HOTAS Control process.

TRACEABILITY
------------
[a] Implements part def HotasControl (fsim_TA_Peripherals.sysml),
    doc comment names this very file: "hotas_standalone.py".
[a] Requirement L2SYS-REQ-01.01b (fsim_Design.xlsx): "The Hotas shall
    continuously poll physical joystick axes and buttons and transmit
    them via UDP." Allocated logical block: Hotas (fsim_LA.sysml),
    supports req_manualFlight (fsim_Requirements.sysml) and
    UC_ManualFlight (fsim_UC.sysml).
[a] Output port: e_hotasControlOut : UdpPort, portNumber = 5005
    (fsim_TA_Peripherals.sysml) -> ports.UDP_HOTAS_CONTROL.

TA ACTION -> CODE MAPPING [b]
-----------------------------
    measureStickDeflection   -> _read_axes() roll/pitch
    measureThrottlePosition  -> _read_axes() throttle
    measureYawTwist          -> _read_axes() yaw
    readSwitches             -> _read_switches()
    formatControlPayload     -> HotasControls.to_json()
    transmitControlData      -> _transmit()

DEVELOPER CHOICES [c] (not in the design specification)
--------------------------------------------------------
- pygame as joystick backend (SDL2; robust HOTAS support on Windows).
- Poll/tx rate 60 Hz: "continuously" is unquantified in the requirement;
  60 Hz halves the 120 Hz physics rate and is far above human input
  bandwidth. Adjustable via --rate.
- Axis roles default to the mapping PROVEN WORKING on the reference
  hardware (legacy hotas_standalone.py, field-verified by the customer,
  2026-07-11): roll=axis0, pitch=axis1, throttle=axis2 [a: legacy file].
  Yaw/rudder is DEVICE-DEPENDENT: some HOTAS throttle units expose it on
  axis3, others axis4 -- the legacy file handles this with "Smart Rudder
  Detection": read both candidates, take whichever has the larger
  deflection [a: legacy file, "Smart Rudder Detection" comment]. Ported
  here as --yaw-axes (default: 3 4).
- Throttle axes report +1..-1 on most hardware; normalized here to 0..1.
- --demo mode synthesizes a gentle stick pattern so integration tests can
  run without physical hardware.
- --debug-axes: raw, unmapped axis dump for empirically identifying
  indices on hardware that doesn't match the defaults above -- no
  guessing, no UDP transmission, just numbers to read off the console
  while you move each control one at a time.

Usage:
    python hotas_standalone.py                 # first joystick, defaults
    python hotas_standalone.py --list          # enumerate devices
    python hotas_standalone.py --debug-axes    # raw axis id, move each control
    python hotas_standalone.py --demo --debug  # no hardware, live readout
    python hotas_standalone.py --yaw-axes 4    # rudder confirmed on axis 4 only
    python hotas_standalone.py --throttle-axis 3 --yaw-axes 2

DEBUG READOUT [c]
------------------
--debug prints what is actually being transmitted, following the same
pattern as the earlier reference implementation (which printed device
info on connect and one line per button edge, e.g. "[CMD] Airbrake
Toggle Sent"): a throttled, in-place status line (roll/pitch/yaw/
throttle, refreshed a few times per second rather than at the full
60 Hz tx rate, so the console stays readable) plus one printed line per
button press/release edge. Use this to verify axis mapping and sign
before trusting the link to the PCS.

--debug-axes is the calibration tool: it bypasses all role mapping and
prints every raw axis index and value, so you can move one control at a
time and read off which index it actually is on your hardware, then
pass that via --roll-axis / --pitch-axis / --throttle-axis / --yaw-axes.
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
# Hardware backend (pygame) -- imported lazily so --demo works without it
# --------------------------------------------------------------------------

class JoystickSource:
    """Wraps one pygame joystick. [c] backend choice.

    Axis roles [a: legacy hotas_standalone.py, field-verified mapping]:
        roll_axis, pitch_axis, throttle_axis : single index each
        yaw_axes : one or more candidate indices; the one with the
            largest |deflection| wins each frame ("Smart Rudder
            Detection" [a: legacy file] -- handles HOTAS units where
            the rudder/twist control's exact axis index is
            device-dependent).
    """

    def __init__(self, index: int, roll_axis: int, pitch_axis: int,
                throttle_axis: int, yaw_axes: list[int], deadzone: float):
        import pygame  # local import: --demo/--list must not require it
        pygame.init()
        pygame.joystick.init()
        self._pygame = pygame
        self._index = index
        self._last_rescan = 0.0
        # [b: bug fix 2026-07-12] do NOT exit when no device is present:
        # wait for hot-plug instead, so starting the transmitter before
        # plugging the HOTAS (or replugging later) just works
        if pygame.joystick.get_count() == 0:
            print("[hotas] no joystick detected -- waiting for device "
                  "(virtual HOTAS active on the PCS meanwhile)...")
            self._js = None
            self._connected = False
        else:
            self._js = pygame.joystick.Joystick(index)
            self._js.init()
        self._roll_axis = roll_axis
        self._pitch_axis = pitch_axis
        self._throttle_axis = throttle_axis
        self._yaw_axes = yaw_axes
        self._deadzone = deadzone
        if self._js is not None:
            self._connected = True
            print(f"[hotas] using device {index}: {self._js.get_name()!r}, "
                  f"{self._js.get_numaxes()} axes, "
                  f"{self._js.get_numbuttons()} buttons")
        print(f"[hotas] axis roles: roll={roll_axis} pitch={pitch_axis} "
              f"throttle={throttle_axis} yaw_candidates={yaw_axes} "
              f"-- run --debug-axes if these are wrong for your hardware")

    def _raw_axis(self, idx: int) -> float:
        if idx >= self._js.get_numaxes():
            return 0.0
        return self._js.get_axis(idx)

    def _axis(self, idx: int) -> float:
        v = self._raw_axis(idx)
        return 0.0 if abs(v) < self._deadzone else max(-1.0, min(1.0, v))

    def raw_axes(self) -> list[float]:
        """All raw, unmapped axis values -- for --debug-axes calibration."""
        self._pygame.event.pump()
        return [self._js.get_axis(i) for i in range(self._js.get_numaxes())]

    def read(self):
        """Returns HotasControls, or None while the device is unplugged
        [b: bug fix 2026-07-12 -- transmitting stale values after a USB
        disconnect kept 'physical precedence' alive on the PCS and
        blocked the virtual HOTAS from engaging]."""
        pg = self._pygame
        for ev in pg.event.get():
            if ev.type == getattr(pg, "JOYDEVICEREMOVED", None):
                if getattr(self, "_connected", True):
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
        if not getattr(self, "_connected", True):
            # [b] fallback rescan every 1 s: some platforms deliver no
            # JOYDEVICEADDED event without a window; re-init the joystick
            # subsystem to force SDL to re-enumerate devices
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
            if not self._connected:
                return None
        pg.event.pump()
        roll = self._axis(self._roll_axis)             # measureStickDeflection
        pitch = self._axis(self._pitch_axis)            # measureStickDeflection
        raw_thr = self._axis(self._throttle_axis)       # measureThrottlePosition
        throttle = (1.0 - raw_thr) / 2.0                # [c] +1..-1 -> 0..1

        # measureYawTwist -- smart pick among candidates [a: legacy file]
        yaw = 0.0
        best_mag = 0.0
        for idx in self._yaw_axes:
            v = self._axis(idx)
            if abs(v) > best_mag:
                best_mag, yaw = abs(v), v

        switches = {                                  # readSwitches
            f"btn{i}": bool(self._js.get_button(i))
            for i in range(self._js.get_numbuttons())
        }
        return HotasControls(roll=roll, pitch=pitch, yaw=yaw,
                             throttle=throttle, switches=switches)


class DemoSource:
    """[c] Synthetic input pattern for hardware-free integration testing."""

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
    this is how you determine --roll-axis/--pitch-axis/--throttle-axis/
    --yaw-axes for hardware that doesn't match the shipped defaults.
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
# Debug console readout [c] -- same spirit as the reference implementation's
# connect banner + per-button-edge prints, generalized to all axes/switches.
# --------------------------------------------------------------------------

class DebugPrinter:
    """Throttled in-place status line + edge-triggered button events."""

    def __init__(self, refresh_hz: float = 5.0):
        self._period = 1.0 / refresh_hz
        self._t_next = 0.0
        self._prev_switches: dict[str, bool] = {}

    def update(self, msg: HotasControls) -> None:
        # Edge events -- always printed immediately, never throttled,
        # mirrors the reference file's "[CMD] Airbrake Toggle Sent".
        for name, pressed in msg.switches.items():
            was = self._prev_switches.get(name, False)
            if pressed and not was:
                print(f"\n[hotas] button DOWN : {name}")
            elif was and not pressed:
                print(f"\n[hotas] button UP   : {name}")
        self._prev_switches = dict(msg.switches)

        # Throttled continuous status line, overwritten in place.
        now = time.monotonic()
        if now < self._t_next:
            return
        self._t_next = now + self._period
        print(f"\r[hotas] seq={msg.seq:6d}  "
              f"roll={msg.roll:+.2f}  pitch={msg.pitch:+.2f}  "
              f"yaw={msg.yaw:+.2f}  throttle={msg.throttle:.2f}   ",
              end="", flush=True)


# --------------------------------------------------------------------------
# Transmit loop
# --------------------------------------------------------------------------

def run(source, rate_hz: float, host: str, port: int,
       debug: bool = False, stop_event=None) -> None:
    # stop_event [c]: optional threading.Event for embedded/test
    # use; the CLI path runs until KeyboardInterrupt as before.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    period = 1.0 / rate_hz
    seq = 0
    printer = DebugPrinter() if debug else None
    print(f"[hotas] transmitting to udp://{host}:{port} at {rate_hz:.0f} Hz")
    if debug:
        print("[hotas] --debug: live readout below "
             "(move axes / press buttons to verify)")
    try:
        while stop_event is None or not stop_event.is_set():
            t_next = time.monotonic() + period
            msg = source.read()                       # measure* / readSwitches
            if msg is None:                           # device unplugged:
                time.sleep(period)                    # stay silent so the
                continue                              # PCS falls back [b]
            msg.seq = seq
            sock.sendto(msg.to_json(), (host, port))  # formatControlPayload
            seq += 1                                  # + transmitControlData
            if printer is not None:
                printer.update(msg)
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\n[hotas] stopped.")
    finally:
        sock.close()


def main(argv=None) -> None:
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
                   help="candidate axis indices for yaw/rudder; the one "
                       "with the larger deflection wins each frame "
                       "(default: 3 4) [a: legacy 'Smart Rudder Detection']")
    p.add_argument("--deadzone", type=float, default=0.05,
                   help="axis deadzone (default 0.05) [c]")
    p.add_argument("--rate", type=float, default=60.0,
                   help="poll/tx rate in Hz (default 60) [c]")
    p.add_argument("--host", default=ports.LOCALHOST,
                   help="PCS host (default 127.0.0.1) [a: TA address]")
    p.add_argument("--port", type=int, default=ports.UDP_HOTAS_CONTROL,
                   help="PCS control port (default 5005) [a: TA portNumber]")
    p.add_argument("--demo", action="store_true",
                   help="synthesize inputs; no hardware needed [c]")
    p.add_argument("--debug", action="store_true",
                   help="print live axis/button readout to console [c]")
    p.add_argument("--debug-axes", action="store_true",
                   help="raw unmapped axis dump for calibration; no tx [c]")
    args = p.parse_args(argv)

    if args.list:
        import pygame
        pygame.init(); pygame.joystick.init()
        n = pygame.joystick.get_count()
        print(f"{n} joystick(s) found:")
        for i in range(n):
            js = pygame.joystick.Joystick(i); js.init()
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