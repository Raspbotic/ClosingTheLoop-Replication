"""Ground-proximity attention getter -- Qt-free. [a: D23] [a: L2SYS-REQ-02.04]

[a: L2SYS-REQ-02.04] "The PCS shall display a ground-proximity attention getter
(e.g. 'PULL UP') when height above ground and vertical speed indicate imminent
ground impact. Attention Getter shall flash intermittently while warning
condition persists."

[a: D23, provisional] "The 02.04 attention getter asserts when the aircraft is
airborne, descending faster than 2 m/s, and height above ground divided by rate
of descent is under 10 s; never above 900 m AGL; never on the ground. It flashes
with a period of about 0.6 s while the condition persists and blanks otherwise."

Also realizes `updateAttentionGetters`
[a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml], whose doc names 02.04 directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# D23, transcribed.
# ---------------------------------------------------------------------------

#: "descending faster than 2 m/s" -- strictly faster, so exactly 2 m/s does not
#: assert.
DESCENT_THRESHOLD_MPS = 2.0

#: "height above ground divided by rate of descent is under 10 s" -- strictly
#: under, so exactly 10 s does not assert.
TIME_TO_IMPACT_LIMIT_S = 10.0

#: "never above 900 m AGL"
MAX_AGL_M = 900.0

#: "flashes with a period of about 0.6 s"
FLASH_PERIOD_S = 0.6

#: The text the requirement gives as an example. [a: L2SYS-REQ-02.04]
WARNING_TEXT = "PULL UP"


def time_to_impact_s(agl_m: float, vspeed_mps: float) -> float | None:
    """Height above ground divided by rate of descent. [a: D23]

    `vspeed_mps` is positive up, matching D19. Returns None when the aircraft
    is not descending, because the quotient is then meaningless rather than
    large.
    """
    rate_of_descent = -vspeed_mps
    if rate_of_descent <= 0.0:
        return None
    return agl_m / rate_of_descent


def ground_proximity_condition(
    agl_m: float, vspeed_mps: float, on_ground: bool
) -> bool:
    """The D23 condition. [a: D23] [a: L2SYS-REQ-02.04]

    Every clause is a separate statement below so that a failing row names the
    clause it failed on:

      1. never on the ground
      2. descending faster than 2 m/s     (strict)
      3. never above 900 m AGL            (so <= 900)
      4. agl / rate-of-descent under 10 s (strict)
    """
    if on_ground:
        return False                                  # 1
    if -vspeed_mps <= DESCENT_THRESHOLD_MPS:
        return False                                  # 2
    if agl_m > MAX_AGL_M:
        return False                                  # 3
    ttl = time_to_impact_s(agl_m, vspeed_mps)
    if ttl is None or ttl >= TIME_TO_IMPACT_LIMIT_S:
        return False                                  # 4
    return True


@dataclass
class AttentionGetter:
    """The flashing strip. [a: D23] [a: D14]

    [a: D14] "The attention-getter strip is empty in the nominal case."

    The clock is injectable so the flash period can be **measured** against a
    clock the suite controls rather than against wall time. [a: D18]
    """

    clock: Callable[[], float] | None = None
    period_s: float = FLASH_PERIOD_S
    text: str = WARNING_TEXT

    def __post_init__(self) -> None:
        import time

        self._clock = self.clock or time.monotonic
        self._asserted = False
        self._asserted_at = 0.0
        self.transitions = 0
        self._last_visible = False

    @property
    def asserted(self) -> bool:
        return self._asserted

    def update(self, agl_m: float, vspeed_mps: float, on_ground: bool) -> bool:
        """Evaluate the condition. Returns whether it is asserted."""
        now = self._clock()
        condition = ground_proximity_condition(agl_m, vspeed_mps, on_ground)
        if condition and not self._asserted:
            self._asserted_at = now                # start the flash cycle
        self._asserted = condition
        return condition

    def visible(self) -> bool:
        """Whether the text is showing **right now**. [a: D23]

        A square wave of period `period_s`: on for the first half, off for the
        second, so one full cycle is one on and one off.
        """
        if not self._asserted:
            return False
        elapsed = self._clock() - self._asserted_at
        phase = (elapsed % self.period_s) / self.period_s
        return phase < 0.5

    def display_text(self) -> str:
        """What the strip shows. Empty in the nominal case. [a: D14]"""
        return self.text if (self._asserted and self.visible()) else ""

    def tick(self) -> bool:
        """Sample `visible()` and count a transition when it changed.

        The suite measures the flash period by counting transitions over a
        window of its own clock. [a: D18]
        """
        now_visible = self.visible()
        if now_visible != self._last_visible:
            self.transitions += 1
            self._last_visible = now_visible
        return now_visible

    def reset(self) -> None:
        self._asserted = False
        self._last_visible = False
        self.transitions = 0
