"""HOTAS transmitter. HotasControl per fsim_TA_Peripherals.sysml.

[a: L2SYS-REQ-01.01b] Continuously polls joystick axes, throttle lever and buttons and
transmits them via UDP to the Virtual Ownship (PCS ioManager, port 5005).
[a: Behaviour B5] button map, smart yaw, hot-plug tolerance.
Legacy hotas_standalone.py is evidence for axis mapping and printout style.

Usage:  python -m flightsim.hotas.hotas_standalone [--demo] [--debug] [--debug-axes] [--list]
"""
from __future__ import annotations

import argparse
import math
import os
import socket
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flightsim.common import ports
from flightsim.common.messages import HotasControls, encode

DEADZONE = 0.05  # [b: legacy hotas_standalone.py "0.05 Deadzones"]
BTN_GEAR = 2      # [a: Behaviour B5] legacy fallback btn2 (legacy file lacks it; finding F3)
BTN_AIRBRAKE = 4  # [a: Behaviour B5] [b: legacy "Airbrake Toggle (Button 4)"]
BTN_WHEELBRAKE = 6  # [a: Behaviour B5] [b: legacy "Continuous hold for Brakes (Button 6)"]
RESCAN_S = 1.0    # [a: Behaviour B5] periodic joystick-subsystem rescan; period [c]


def _dz(v):
    return v if abs(v) > DEADZONE else 0.0


def _clip(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class AxisMapper:
    """Pure mapping from raw device axes/buttons to wire fields.

    measureStickDeflection / measureThrottlePosition / measureYawTwist / readSwitches
    [a: fsim_TA_Peripherals.sysml#HotasControl]. Axis indices: roll 0, pitch 1, throttle 2,
    yaw candidates 3/4 [b: legacy hotas_standalone.py].
    Pitch sign: SDL reports stick forward as negative axis 1 [c: not measurable without
    hardware in the sandbox; settled by R-11 hardware check]; the wire carries stick back = +1
    [a: Behaviour B8], so pitch = +axis1. Legacy inverted (finding F4).
    """

    def __init__(self, invert_pitch=False, btn_gear=BTN_GEAR, btn_airbrake=BTN_AIRBRAKE,
                 btn_wheelbrake=BTN_WHEELBRAKE):
        self.invert_pitch = invert_pitch
        # button indices configurable per device (field defect: brake button not on index 6) [c]
        self.btn_gear, self.btn_airbrake, self.btn_wheelbrake = btn_gear, btn_airbrake, btn_wheelbrake  # [c] field fallback for open item M12 (pitch sign unmeasured)
        self.prev_buttons = {}
        self.gear_toggles = 0
        self.airbrake_toggles = 0

    def map(self, axes, buttons):
        n = len(axes)
        roll = _clip(_dz(axes[0] if n > 0 else 0.0), -1, 1)
        pitch = _clip(_dz(axes[1] if n > 1 else 0.0), -1, 1)
        if self.invert_pitch:
            pitch = -pitch
        throttle_raw = axes[2] if n > 2 else 1.0
        throttle = _clip((1.0 - throttle_raw) / 2.0, 0.0, 1.0)  # [b: legacy]
        a3 = axes[3] if n > 3 else 0.0
        a4 = axes[4] if n > 4 else 0.0
        yaw = _clip(_dz(a3 if abs(a3) > abs(a4) else a4), -1, 1)  # smart yaw [a: Behaviour B5]
        events = []
        for i, pressed in enumerate(buttons):
            was = self.prev_buttons.get(i, False)
            if pressed and not was:
                events.append(f"[BTN {i}] pressed")
                if i == self.btn_gear:
                    self.gear_toggles += 1
                    events.append("[CMD] Gear Toggle Sent")
                elif i == self.btn_airbrake:
                    self.airbrake_toggles += 1
                    events.append("[CMD] Airbrake Toggle Sent")
            self.prev_buttons[i] = bool(pressed)
        wb = self.btn_wheelbrake
        wheel_brake = bool(wb is not None and 0 <= wb < len(buttons) and buttons[wb])
        return dict(roll=roll, pitch=pitch, yaw=yaw, throttle=throttle,
                    gear_toggles=self.gear_toggles, airbrake_toggles=self.airbrake_toggles,
                    wheel_brake=wheel_brake), events


class DemoSource:
    """Hardware-free synthetic source. Clock injectable. [a: Conv]"""
    name = "DEMO (synthetic)"

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.t0 = clock()

    def poll(self):
        t = self.clock() - self.t0
        axes = [0.3 * math.sin(0.5 * t), 0.2 * math.sin(0.3 * t), 1.0 - 2 * (0.5 + 0.2 * math.sin(0.1 * t)),
                0.1 * math.sin(0.7 * t), 0.0]
        buttons = [False] * 8
        return axes, buttons


class PygameSource:
    """Physical device through pygame (lazy import: only the HOTAS venv has pygame).
    Hot-plug: start and wait with no device; suspend on removal; resume on reconnect;
    periodic rescan fallback. [a: Behaviour B5]"""

    def __init__(self, log=print, clock=time.monotonic):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "hide")
        import pygame  # lazy [a: Conv]
        self.pg = pygame
        pygame.init()
        pygame.joystick.init()
        self.log = log
        self.clock = clock
        self.js = None
        self.last_scan = -1e9
        self.name = "none"
        self._attach()

    def _attach(self):
        pg = self.pg
        if pg.joystick.get_count() > 0:
            self.js = pg.joystick.Joystick(0)
            self.js.init()
            self.name = self.js.get_name()
            self.log(f"HOTAS Connected: {self.name}")
            self.log(f"Detected {self.js.get_numbuttons()} Buttons and {self.js.get_numaxes()} Axes.\n")
        else:
            self.js = None

    def poll(self):
        pg = self.pg
        for ev in pg.event.get():
            if ev.type == getattr(pg, "JOYDEVICEREMOVED", -1):
                self.log("HOTAS removed: transmission suspended.")
                self.js = None
            elif ev.type == getattr(pg, "JOYDEVICEADDED", -2) and self.js is None:
                self._attach()
        if self.js is None:
            now = self.clock()
            if now - self.last_scan >= RESCAN_S:
                self.last_scan = now
                pg.joystick.quit()
                pg.joystick.init()
                self._attach()
            if self.js is None:
                return None
        try:
            axes = [self.js.get_axis(i) for i in range(self.js.get_numaxes())]
            buttons = [bool(self.js.get_button(i)) for i in range(self.js.get_numbuttons())]
        except Exception:
            self.log("HOTAS read failed: transmission suspended.")
            self.js = None
            return None
        return axes, buttons

    @staticmethod
    def list_devices(log=print):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "hide")
        import pygame
        pygame.init()
        pygame.joystick.init()
        n = pygame.joystick.get_count()
        log(f"{n} joystick(s) found")
        for i in range(n):
            j = pygame.joystick.Joystick(i)
            j.init()
            log(f"  [{i}] {j.get_name()}: {j.get_numaxes()} axes, {j.get_numbuttons()} buttons")
        return n


class HotasTransmitter:
    """formatControlPayload + transmitControlData [a: fsim_TA_Peripherals.sysml#HotasControl].
    Rate from ports.HOTAS_RATE_HZ; stoppable via stop_event. [a: Conv]"""

    def __init__(self, source, port=ports.HOTAS_CONTROL_PORT, host=ports.LOOPBACK,
                 rate_hz=ports.HOTAS_RATE_HZ, debug=False, debug_axes=False, log=print,
                 clock=time.monotonic, sleep=time.sleep, invert_pitch=False, buttons=None):
        self.source = source
        self.addr = (host, port)
        self.period = 1.0 / rate_hz
        self.debug, self.debug_axes, self.log = debug, debug_axes, log
        self.clock, self.sleep = clock, sleep
        self.mapper = AxisMapper(invert_pitch=invert_pitch, **(buttons or {}))
        self.seq = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._last_print = None

    def step(self):
        polled = self.source.poll()
        if polled is None:
            return False  # suspended: PCS falls back to virtual controls [a: Behaviour B5]
        fields, events = self.mapper.map(*polled)
        self.seq += 1
        msg = HotasControls(seq=self.seq, **fields)
        try:
            self.sock.sendto(encode(msg), self.addr)
        except OSError:
            pass
        if self.debug:
            for e in events:
                self.log(e)
            key = (round(fields["roll"], 2), round(fields["pitch"], 2), round(fields["throttle"], 2))
            if key != self._last_print:
                self._last_print = key
                self.log(f"Roll {fields['roll']:+.2f} Pitch {fields['pitch']:+.2f} Yaw {fields['yaw']:+.2f} Thr {fields['throttle']:.2f}")
        if self.debug_axes:
            self.log("axes " + " ".join(f"{a:+.2f}" for a in polled[0]))
        return True

    def run(self, stop_event=None):
        stop_event = stop_event or threading.Event()
        nxt = self.clock()
        try:
            while not stop_event.is_set():
                self.step()
                nxt += self.period
                delay = nxt - self.clock()
                if delay > 0:
                    stop_event.wait(delay)
                else:
                    nxt = self.clock()
        finally:
            self.sock.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="HOTAS UDP transmitter")
    ap.add_argument("--demo", action="store_true", help="hardware-free synthetic source")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--debug-axes", action="store_true")
    ap.add_argument("--list", action="store_true", help="list game controllers and exit")
    ap.add_argument("--invert-pitch", action="store_true", help="invert axis 1 if stick back reads negative")
    ap.add_argument("--btn-gear", type=int, default=BTN_GEAR, help=f"gear toggle button index (default {BTN_GEAR})")
    ap.add_argument("--btn-airbrake", type=int, default=BTN_AIRBRAKE, help=f"air-brake toggle button index (default {BTN_AIRBRAKE})")
    ap.add_argument("--btn-brake", type=int, default=BTN_WHEELBRAKE, help=f"wheel-brake (hold) button index (default {BTN_WHEELBRAKE})")
    a = ap.parse_args(argv)
    if a.list:
        PygameSource.list_devices()
        return 0
    print("===========================================")
    print(f" Starting HOTAS UDP Transmitter (Port {ports.HOTAS_CONTROL_PORT})")
    print("===========================================")
    src = DemoSource() if a.demo else PygameSource()
    if not a.demo and src.js is None:
        print("No USB joystick detected. Waiting for device...")
    print(f"Buttons: gear={a.btn_gear} airbrake={a.btn_airbrake} wheel-brake={a.btn_brake} "
          "(--debug prints the index of every button pressed)")
    tx = HotasTransmitter(src, debug=a.debug, debug_axes=a.debug_axes, invert_pitch=a.invert_pitch,
                          buttons=dict(btn_gear=a.btn_gear, btn_airbrake=a.btn_airbrake, btn_wheelbrake=a.btn_brake))
    try:
        tx.run()
    except KeyboardInterrupt:
        print("\nShutting down HOTAS transmitter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
