"""HOTAS transmitter -- the only module that runs under .venv_hotas.

[a: L2SYS-REQ-01.01b] "The HOTAS shall continuously poll the physical joystick
axes, throttle lever and buttons, and transmit the associated signals via UDP to
the Virtual Ownship."

[a: fsim_TA_Peripherals.sysml] HotasControl's six actions are realized here:
    measureStickDeflection  -> Reading.roll / Reading.pitch
    measureThrottlePosition -> Reading.throttle
    measureYawTwist         -> Reading.yaw (D7 candidate selection)
    readSwitches            -> Reading.switches
    formatControlPayload    -> Transmitter._payload
    transmitControlData     -> Transmitter._send
and `runtime.version = "3.11"` with the rationale "pygame: no cp314 wheels
available" -- independently confirmed from package metadata:
`tools/check_wheels.py requirements_hotas.txt --python 3.14 --platform win_amd64`
rejects pygame==2.6.1, and the same command with --python 3.11 accepts it.

[a: Sec.4.4] The legacy `hotas_standalone.py` is authoritative for the axis
indices (roll 0, pitch 1, throttle 2, yaw candidates 3 and 4), the throttle
normalization, and the debug-printout format including its on-change
suppression. It is **not** authoritative for signs, for the wire format (D1) or
for the button map (D7).

[a: D7] btn2 = landing-gear toggle; btn4 = air-brake toggle; btn6 = momentary
wheel brake. "The transmitter puts raw pressed states on the wire; toggle edges
for btn2 and btn4 are detected by the receiver" -- so this module emits state,
never an edge. The receiver-side edge detection is in flightsim/pcs/io_manager.py.

pygame is imported **lazily**, inside PygameDeviceSource.open(), so this module
stays importable from .venv_fsim where pygame is absent. [a: R-4]
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from flightsim.common import ports
from flightsim.common.messages import HotasControls, SeqCounter, encode

# ---------------------------------------------------------------------------
# Constants taken from the legacy transmitter [a: Sec.4.4]
# ---------------------------------------------------------------------------

#: [a: Sec.4.4] "the axis indices (roll 0, pitch 1, throttle 2, yaw candidates 3
#: and 4)". The legacy file reads `get_axis(0)` for roll, `get_axis(1)` for
#: pitch, `get_axis(2)` for throttle, and `get_axis(3)`/`get_axis(4)` for yaw.
AXIS_ROLL = 0
AXIS_PITCH = 1
AXIS_THROTTLE = 2
AXIS_YAW_CANDIDATES = (3, 4)

#: [a: Sec.4.4] the legacy file's deadzone: `abs(raw) > 0.05` on roll, pitch and
#: yaw. Throttle has none there, and none is added here.
DEADZONE = 0.05

#: [a: D7] the three buttons the Decision Annex names.
BTN_GEAR = 2
BTN_AIR_BRAKE = 4
BTN_WHEEL_BRAKE = 6
NAMED_BUTTONS = (BTN_GEAR, BTN_AIR_BRAKE, BTN_WHEEL_BRAKE)

#: [a: Sec.4.4] "a 60 Hz transmit loop" -- the legacy file's `time.sleep(1/60.0)`.
#: [a: R-4] "a 60 Hz transmit loop with an optional stop_event".
TRANSMIT_HZ = 60.0
TRANSMIT_PERIOD_S = 1.0 / TRANSMIT_HZ

#: [a: R-4] "resume on reconnect with a 1 Hz rescan fallback".
RESCAN_HZ = 1.0
RESCAN_PERIOD_S = 1.0 / RESCAN_HZ


def apply_deadzone(raw: float, deadzone: float = DEADZONE) -> float:
    """[a: Sec.4.4] `value if abs(value) > deadzone else 0.0`.

    Note the legacy comparison is strictly greater, so exactly +-0.05 is
    suppressed. Reproduced exactly.
    """
    return raw if abs(raw) > deadzone else 0.0


def normalize_throttle(raw: float) -> float:
    """[a: Sec.4.4] `throttle = (1.0 - throttle_raw) / 2.0`.

    Maps a raw axis of +1 to 0.0 and -1 to 1.0, i.e. lever forward is full
    power. Clamped into [0, 1] because D1 enforces that range and a physical
    axis can read slightly outside its nominal travel. [b: D1]
    """
    return min(1.0, max(0.0, (1.0 - raw) / 2.0))


def select_yaw(axis3: float, axis4: float, deadzone: float = DEADZONE) -> float:
    """[a: D7] "HOTAS yaw axis: the larger deflection of candidate axes 3 and 4."

    [a: Sec.4.4] the legacy expression is
    `yaw_raw = axis3 if abs(axis3) > abs(axis4) else axis4`.

    Note the tie: with equal magnitudes the legacy file takes axis4, and so does
    this. Making the choice explicit rather than incidental keeps the selection
    stable frame to frame instead of alternating. [b: Sec.4.4, D7]
    """
    raw = axis3 if abs(axis3) > abs(axis4) else axis4
    return apply_deadzone(raw, deadzone)


# ---------------------------------------------------------------------------
# Device sources
# ---------------------------------------------------------------------------
@dataclass
class Reading:
    """One poll of the device. Signs are pilot-side, per D1/D10. [b]"""

    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    throttle: float = 0.0
    switches: dict[str, bool] = field(default_factory=dict)


class DeviceSource(Protocol):
    """What the transmit loop needs from a device.

    A Protocol, so the suite can inject a stub without pygame and without a
    joystick. [a: D18, Sec.3 criterion 6]
    """

    def open(self) -> bool: ...
    def poll(self) -> Reading | None: ...
    def close(self) -> None: ...
    @property
    def connected(self) -> bool: ...
    @property
    def description(self) -> str: ...


class PygameDeviceSource:
    """A physical HOTAS via pygame. **pygame is imported here, not at module
    level** -- this module must stay importable from .venv_fsim. [a: R-4]
    """

    def __init__(self, index: int = 0) -> None:
        self.index = index
        self._pygame = None
        self._joystick = None
        self._num_axes = 0
        self._num_buttons = 0
        self._name = ""

    # -- lazy import ------------------------------------------------------
    def _import_pygame(self):
        if self._pygame is None:
            import os

            # [a: Sec.4.4] the legacy file sets this before importing pygame.
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "hide")
            import pygame  # noqa: PLC0415 -- deliberately lazy

            pygame.init()
            pygame.joystick.init()
            self._pygame = pygame
        return self._pygame

    @property
    def connected(self) -> bool:
        return self._joystick is not None

    @property
    def description(self) -> str:
        if not self.connected:
            return "no device"
        return f"{self._name} ({self._num_buttons} buttons, {self._num_axes} axes)"

    def open(self) -> bool:
        pygame = self._import_pygame()
        if pygame.joystick.get_count() <= self.index:
            # [a: Sec.4.4] the legacy file's message, verbatim.
            return False
        self._joystick = pygame.joystick.Joystick(self.index)
        self._joystick.init()
        self._num_axes = self._joystick.get_numaxes()
        self._num_buttons = self._joystick.get_numbuttons()
        self._name = self._joystick.get_name()
        return True

    def _axis(self, i: int, default: float = 0.0) -> float:
        if self._joystick is None or self._num_axes <= i:
            return default
        return float(self._joystick.get_axis(i))

    def poll(self) -> Reading | None:
        if self._joystick is None:
            return None
        pygame = self._pygame
        # [a: R-4] hot-plug: suspend on JOYDEVICEREMOVED.
        for event in pygame.event.get():
            if event.type == getattr(pygame, "JOYDEVICEREMOVED", -1):
                self.close()
                return None
        pygame.event.pump()

        roll = apply_deadzone(self._axis(AXIS_ROLL))
        # [a: Sec.4.4] "Inverted for correct sign: Stick forward = Pitch Down".
        # Sec.4.4 withholds authority over signs; D10 fixes the pilot side:
        # "Drag-down = stick back = pitch +1". Pulling the stick back is a
        # negative raw axis on a standard joystick, so the negation makes
        # stick-back +1, which is the D10 convention. [a: D10] [b: Sec.4.4]
        pitch = apply_deadzone(-self._axis(AXIS_PITCH))
        # [a: Sec.4.4] `get_axis(2) if num_axes > 2 else 1.0` -- absent axis
        # reads +1, which normalizes to throttle 0.0, i.e. idle.
        throttle = normalize_throttle(self._axis(AXIS_THROTTLE, default=1.0))
        yaw = select_yaw(self._axis(AXIS_YAW_CANDIDATES[0]),
                         self._axis(AXIS_YAW_CANDIDATES[1]))

        switches = {
            str(i): bool(self._joystick.get_button(i))
            for i in range(self._num_buttons)
        }
        return Reading(roll=roll, pitch=pitch, yaw=yaw, throttle=throttle,
                       switches=switches)

    def close(self) -> None:
        if self._joystick is not None:
            try:
                self._joystick.quit()
            except Exception:
                pass
            self._joystick = None


class DemoDeviceSource:
    """Hardware-free synthetic source for `--demo`. [a: R-4]

    Deterministic in its own time base, so a row that uses it asserts on values
    it can predict rather than on whatever a physical stick happened to be
    doing. [b: D18]
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._t0 = self._clock()
        self._open = False

    @property
    def connected(self) -> bool:
        return self._open

    @property
    def description(self) -> str:
        return "demo (synthetic, no hardware)"

    def open(self) -> bool:
        self._t0 = self._clock()
        self._open = True
        return True

    def poll(self) -> Reading | None:
        if not self._open:
            return None
        t = self._clock() - self._t0
        roll = apply_deadzone(0.6 * math.sin(2.0 * math.pi * t / 11.0))
        pitch = apply_deadzone(0.4 * math.sin(2.0 * math.pi * t / 7.0))
        yaw = apply_deadzone(0.3 * math.sin(2.0 * math.pi * t / 13.0))
        throttle = 0.5 + 0.5 * math.sin(2.0 * math.pi * t / 17.0)
        throttle = min(1.0, max(0.0, throttle))
        switches = {str(i): False for i in NAMED_BUTTONS}
        # a slow square wave on the wheel brake, so a receiver-side row has a
        # switch that actually changes
        switches[str(BTN_WHEEL_BRAKE)] = (int(t) % 10) < 2
        return Reading(roll=roll, pitch=pitch, yaw=yaw, throttle=throttle,
                       switches=switches)

    def close(self) -> None:
        self._open = False


# ---------------------------------------------------------------------------
# Debug printout [a: Sec.4.4]
# ---------------------------------------------------------------------------
class DebugPrinter:
    """The legacy printout, with its on-change suppression.

    [a: Sec.4.4] the legacy file is authoritative for "the debug-printout format
    including its on-change suppression". Reported in docs/MODEL_INTAKE.md F-6:
    the supplied file declares `self.last_pitch`, `self.last_roll` and
    `self.last_throttle` "to prevent axis-spam in the console" but never reads
    them, so the mechanism is present in the file and the printout that would
    use it is not. This class implements the mechanism the file names and
    reproduces the strings the file does contain, verbatim. [b: Sec.4.4]
    """

    #: [a: Sec.4.4] the legacy strings, unchanged.
    NO_DEVICE = "No USB joystick detected."
    CONNECTED = "HOTAS Connected: {name}"
    COUNTS = "Detected {buttons} Buttons and {axes} Axes.\n"
    AIRBRAKE_TOGGLE = "[CMD] Airbrake Toggle Sent"

    #: Below this an axis has not "changed". Same magnitude as the deadzone, so
    #: the console is quiet for a stick at rest. [c] -- what would settle it: a
    #: stated print threshold, which the legacy file does not give (its
    #: comparison was never written).
    EPSILON = 0.01

    def __init__(self, out=None, axes: bool = False) -> None:
        self._out = out if out is not None else sys.stdout
        self._axes = axes
        # the three memory variables the legacy file declares [a: Sec.4.4]
        self.last_roll = 0.0
        self.last_pitch = 0.0
        self.last_throttle = 0.0
        self._last_switches: dict[str, bool] = {}
        self._primed = False

    def write(self, text: str) -> None:
        print(text, file=self._out)

    def device_absent(self) -> None:
        self.write(self.NO_DEVICE)

    def device_present(self, name: str, buttons: int, axes: int) -> None:
        self.write(self.CONNECTED.format(name=name))
        self.write(self.COUNTS.format(buttons=buttons, axes=axes))

    def on_reading(self, reading: Reading) -> None:
        """Print only what changed. [a: Sec.4.4]"""
        if self._axes:
            changed = (
                not self._primed
                or abs(reading.roll - self.last_roll) > self.EPSILON
                or abs(reading.pitch - self.last_pitch) > self.EPSILON
                or abs(reading.throttle - self.last_throttle) > self.EPSILON
            )
            if changed:
                self.write(
                    f"[AXES] roll={reading.roll:+.3f} "
                    f"pitch={reading.pitch:+.3f} "
                    f"yaw={reading.yaw:+.3f} "
                    f"throttle={reading.throttle:.3f}"
                )
                self.last_roll = reading.roll
                self.last_pitch = reading.pitch
                self.last_throttle = reading.throttle
                self._primed = True

        for key, pressed in reading.switches.items():
            was = self._last_switches.get(key, False)
            if pressed and not was:
                # [a: Sec.4.4] the legacy file prints this on the button-4 press
                # edge. The *edge* here is a console event only: the wire still
                # carries the raw state. [a: D7]
                if int(key) == BTN_AIR_BRAKE:
                    self.write(self.AIRBRAKE_TOGGLE)
                else:
                    self.write(f"[CMD] Button {key} pressed")
        self._last_switches = dict(reading.switches)


# ---------------------------------------------------------------------------
# Transmitter
# ---------------------------------------------------------------------------
class Transmitter:
    """The 60 Hz transmit loop. [a: L2SYS-REQ-01.01b] [a: Sec.4.4]

    Hot-plug tolerant [a: R-4]:
      * start with no device and wait, rescanning at 1 Hz;
      * suspend when the device source reports it is gone;
      * resume on reconnect.

    Stoppable [a: D15]: `stop_event` ends the loop and `close()` releases the
    socket, after which nothing is on the wire.
    """

    def __init__(
        self,
        source: DeviceSource,
        host: str = ports.LOOPBACK,
        port: int = ports.HOTAS_TO_PCS,
        sock: socket.socket | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        printer: DebugPrinter | None = None,
        debug: bool = False,
    ) -> None:
        self.source = source
        self.host = host
        self.port = port
        self._sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._owns_socket = sock is None
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self.printer = printer
        self.debug = debug
        self.seq = SeqCounter()
        self.sent = 0
        self._announced_absent = False
        self._next_rescan = 0.0

    # -- formatControlPayload [a: fsim_TA_Peripherals.sysml] --------------
    def _payload(self, reading: Reading) -> bytes:
        controls = HotasControls(
            seq=self.seq.next(),
            roll=reading.roll,
            pitch=reading.pitch,
            yaw=reading.yaw,
            throttle=reading.throttle,
            switches=dict(reading.switches),
        )
        return encode(controls)

    # -- transmitControlData [a: fsim_TA_Peripherals.sysml] ---------------
    def _send(self, payload: bytes) -> None:
        try:
            self._sock.sendto(payload, (self.host, self.port))
            self.sent += 1
        except OSError:
            # [a: Sec.4.4] the legacy file swallows send failures. A transmitter
            # that dies because nothing is listening would violate the
            # requirement to poll and transmit continuously. [b: L2SYS-REQ-01.01b]
            pass

    def _ensure_open(self) -> bool:
        """Open the device, rescanning at 1 Hz while it is absent. [a: R-4]"""
        if self.source.connected:
            return True
        now = self._clock()
        if now < self._next_rescan:
            return False
        self._next_rescan = now + RESCAN_PERIOD_S
        if self.source.open():
            self._announced_absent = False
            if self.printer:
                desc = self.source.description
                self.printer.write(DebugPrinter.CONNECTED.format(name=desc))
            return True
        if self.printer and not self._announced_absent:
            self.printer.device_absent()
            self._announced_absent = True
        return False

    def iterate(self) -> bool:
        """One poll-and-transmit. True when a datagram went out."""
        if not self._ensure_open():
            return False
        reading = self.source.poll()
        if reading is None:
            # the source reported the device gone; rescan will pick it up again
            self._announced_absent = False
            self._next_rescan = self._clock() + RESCAN_PERIOD_S
            return False
        if self.printer and self.debug:
            self.printer.on_reading(reading)
        self._send(self._payload(reading))
        return True

    def run(
        self,
        stop_event: threading.Event | None = None,
        max_iterations: int | None = None,
    ) -> int:
        """Run the loop until `stop_event` is set. [a: D15] [a: R-4]

        Returns the number of datagrams sent. `max_iterations` bounds a test
        run without requiring a timer.
        """
        stop_event = stop_event or threading.Event()
        n = 0
        while not stop_event.is_set():
            if max_iterations is not None and n >= max_iterations:
                break
            started = self._clock()
            self.iterate()
            n += 1
            elapsed = self._clock() - started
            remaining = TRANSMIT_PERIOD_S - elapsed
            if remaining > 0:
                self._sleep(remaining)
        return self.sent

    def close(self) -> None:
        self.source.close()
        if self._owns_socket:
            try:
                self._sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# --list
# ---------------------------------------------------------------------------
def list_devices(out=None) -> int:
    """`--list`: enumerate attached joysticks. [a: R-4]

    pygame is imported here and nowhere above, so `--list` is the only code path
    in this module that needs it besides the physical source itself.
    """
    out = out if out is not None else sys.stdout
    try:
        import os

        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "hide")
        import pygame
    except ImportError:
        print("pygame is not installed in this environment.", file=out)
        print("The HOTAS transmitter runs in .venv_hotas (Python 3.11);", file=out)
        print("run setup_venvs.bat, then use .venv_hotas\\Scripts\\python.exe.",
              file=out)
        return 2
    pygame.init()
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    if count == 0:
        print(DebugPrinter.NO_DEVICE, file=out)
        return 0
    for i in range(count):
        js = pygame.joystick.Joystick(i)
        js.init()
        print(f"[{i}] {js.get_name()}  "
              f"axes={js.get_numaxes()} buttons={js.get_numbuttons()}", file=out)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m flightsim.hotas_standalone",
        description="HOTAS UDP transmitter. [a: L2SYS-REQ-01.01b]",
    )
    p.add_argument("--demo", action="store_true",
                   help="hardware-free synthetic source; no joystick needed")
    p.add_argument("--debug", action="store_true",
                   help="print switch changes, suppressed while unchanged")
    p.add_argument("--debug-axes", action="store_true",
                   help="also print axis values, suppressed while unchanged")
    p.add_argument("--list", action="store_true",
                   help="list attached joysticks and exit")
    p.add_argument("--host", default=ports.LOOPBACK)
    p.add_argument("--port", type=int, default=ports.HOTAS_TO_PCS)
    p.add_argument("--index", type=int, default=0, help="joystick index")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        return list_devices()

    print("===========================================")
    print(f" Starting HOTAS UDP Transmitter (Port {args.port})")
    print("===========================================")

    source: DeviceSource = (
        DemoDeviceSource() if args.demo else PygameDeviceSource(args.index)
    )
    debug = args.debug or args.debug_axes
    printer = DebugPrinter(axes=args.debug_axes) if debug else None
    tx = Transmitter(source, host=args.host, port=args.port,
                     printer=printer, debug=debug)
    stop = threading.Event()
    try:
        tx.run(stop_event=stop)
    except KeyboardInterrupt:
        print("\nShutting down HOTAS transmitter.")
    finally:
        stop.set()
        tx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
