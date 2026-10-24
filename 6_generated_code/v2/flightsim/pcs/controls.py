"""Control arbitration -- Qt-free. [a: D7] [a: D10] [a: L2SYS-REQ-01.08b]

Two things live here, both of which the requirements name and neither of which
needs a display:

1. **Receiver-side toggle-edge detection.**
   [a: D7] "btn2 = landing-gear toggle; btn4 = air-brake toggle; btn6 =
   momentary wheel brake. ... The transmitter puts raw pressed states on the
   wire; toggle edges for btn2 and btn4 are detected by the receiver; a held
   button does not re-toggle."

2. **Physical / virtual precedence.**
   [a: L2SYS-REQ-01.08b] "on-screen virtual flight controls (stick, throttle,
   pedals) usable when the physical HOTAS is absent; physical HOTAS input shall
   take precedence when present."
   [a: D10] "Physical-precedence window 1.0 s: a fresh datagram on 5005 takes
   precedence; the widget greys out and its title changes. Stick and pedals
   return to neutral on any mouse release. On loss of the physical device,
   stick and pedals centre and the throttle retains its last position. The
   widget mirrors live physical commands at 10 Hz while locked; mirroring is
   display-only."
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from flightsim.common.messages import PHYSICAL_PRECEDENCE_S, HotasControls

# ---------------------------------------------------------------------------
# D7 button map, transcribed.
# ---------------------------------------------------------------------------
BTN_GEAR = 2            # [a: D7] "btn2 = landing-gear toggle"
BTN_AIR_BRAKE = 4       # [a: D7] "btn4 = air-brake toggle"
BTN_WHEEL_BRAKE = 6     # [a: D7] "btn6 = momentary wheel brake"

#: [a: D10] "Physical-precedence window 1.0 s"
PRECEDENCE_WINDOW_S = PHYSICAL_PRECEDENCE_S

#: [a: D10] "The widget mirrors live physical commands at 10 Hz while locked"
MIRROR_HZ = 10.0
MIRROR_PERIOD_S = 1.0 / MIRROR_HZ


@dataclass(frozen=True)
class ToggleState:
    """The two toggles D7 puts on the receiver side."""

    gear_down: bool = True          # [a: D8] gear down at t0
    air_brake: bool = False         # [a: D8] air brake off at t0


class SwitchEdgeDetector:
    """Receiver-side edge detection for btn2 and btn4. [a: D7]

    "a held button does not re-toggle" -- so the toggle fires on the
    **rising edge** only, and the held state that follows changes nothing.
    btn6 is momentary and is read as a level, not an edge.
    """

    def __init__(self, initial: ToggleState | None = None) -> None:
        self.state = initial or ToggleState()
        self._previous: dict[int, bool] = {}
        self.wheel_brake = 0.0
        self.toggles = 0

    @staticmethod
    def _pressed(switches: dict, index: int) -> bool:
        # the wire carries string keys [a: D1]
        value = switches.get(str(index), switches.get(index, False))
        return bool(value)

    def apply(self, controls: HotasControls) -> ToggleState:
        """Fold one datagram's raw switch states into the toggles. [a: D7]"""
        switches = controls.switches or {}
        gear_now = self._pressed(switches, BTN_GEAR)
        brake_now = self._pressed(switches, BTN_AIR_BRAKE)

        state = self.state
        if gear_now and not self._previous.get(BTN_GEAR, False):
            state = replace(state, gear_down=not state.gear_down)
            self.toggles += 1
        if brake_now and not self._previous.get(BTN_AIR_BRAKE, False):
            state = replace(state, air_brake=not state.air_brake)
            self.toggles += 1

        self._previous[BTN_GEAR] = gear_now
        self._previous[BTN_AIR_BRAKE] = brake_now

        # [a: D7] "btn6 = momentary wheel brake" -- a level, not an edge
        self.wheel_brake = 1.0 if self._pressed(switches, BTN_WHEEL_BRAKE) \
            else 0.0

        self.state = state
        return state


@dataclass(frozen=True)
class ControlSample:
    """One set of pilot-side axis commands. [a: D1]"""

    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    throttle: float = 0.0


class PrecedenceArbiter:
    """Physical takes precedence over virtual, for 1.0 s. [a: D10]

    The clock is injectable so the window can be verified against a clock the
    suite controls rather than by sleeping. [a: D18]
    """

    def __init__(
        self,
        window_s: float = PRECEDENCE_WINDOW_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self.window_s = window_s
        self._clock = clock or time.monotonic
        self._last_physical_at: float | None = None
        self._physical = ControlSample()
        self._virtual = ControlSample()
        #: [a: D10] "the throttle retains its last position" on loss
        self._retained_throttle = 0.0
        #: Set once the pilot moves the virtual throttle after a loss, so the
        #: retained position stops overriding a deliberate movement. [b: D10]
        self._virtual_touched_throttle = False

    # -- inputs ---------------------------------------------------------
    def on_physical(self, controls: HotasControls) -> None:
        """A fresh datagram on 5005. [a: D10]"""
        self._physical = ControlSample(
            roll=controls.roll, pitch=controls.pitch,
            yaw=controls.yaw, throttle=controls.throttle,
        )
        self._retained_throttle = controls.throttle
        self._last_physical_at = self._clock()

    def on_virtual(self, sample: ControlSample) -> None:
        self._virtual = sample

    # -- state ----------------------------------------------------------
    @property
    def physical_active(self) -> bool:
        """Whether a physical datagram arrived within the window. [a: D10]"""
        if self._last_physical_at is None:
            return False
        return (self._clock() - self._last_physical_at) < self.window_s

    @property
    def physical_sample(self) -> ControlSample:
        """What the physical device last sent -- for the display-only mirror.
        [a: D10] "mirroring is display-only"."""
        return self._physical

    @property
    def source(self) -> str:
        return "physical" if self.physical_active else "virtual"

    def resolve(self) -> ControlSample:
        """The commands that reach the aircraft. [a: L2SYS-REQ-01.08b]"""
        if self.physical_active:
            return self._physical
        if self._last_physical_at is None:
            # no physical device has ever been seen: the virtual controls fly
            # the aircraft [a: L2SYS-REQ-01.08b]
            return self._virtual
        # [a: D10] "On loss of the physical device, stick and pedals centre and
        # the throttle retains its last position."
        return ControlSample(
            roll=self._virtual.roll,
            pitch=self._virtual.pitch,
            yaw=self._virtual.yaw,
            throttle=(self._virtual.throttle
                      if self._virtual_touched_throttle
                      else self._retained_throttle),
        )

    def on_virtual_throttle(self, value: float) -> None:
        self._virtual = replace(self._virtual, throttle=value)
        self._virtual_touched_throttle = True

    def centre_stick_and_pedals(self) -> ControlSample:
        """[a: D10] "Stick and pedals return to neutral on any mouse release."

        Also the loss behaviour: "stick and pedals centre and the throttle
        retains its last position" -- so throttle is untouched here.
        """
        self._virtual = replace(self._virtual, roll=0.0, pitch=0.0, yaw=0.0)
        return self._virtual


class MirrorTimer:
    """The 10 Hz display-only mirror. [a: D10]

    Injectable clock, so the rate is measured rather than slept through.
    [a: D18]
    """

    def __init__(self, period_s: float = MIRROR_PERIOD_S,
                 clock: Callable[[], float] | None = None) -> None:
        import time

        self.period_s = period_s
        self._clock = clock or time.monotonic
        self._next = self._clock()
        self.updates = 0

    def due(self) -> bool:
        now = self._clock()
        if now >= self._next:
            self._next = now + self.period_s
            self.updates += 1
            return True
        return False
