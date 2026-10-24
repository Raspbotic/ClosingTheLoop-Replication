"""Autopilot laws -- Qt-free. [a: D11] [a: L2SYS-REQ-01.11]

[a: L2SYS-REQ-01.11] "The PCS shall provide a Navigation Controls Panel for
setting the autopilot targets - Altitude (m), Speed (m/s), Heading (deg) - and
for engaging/disengaging the AP (heading hold), AH (altitude hold) and AT
(autothrottle) modes."

[a: D11] Engaging AP, AH or AT captures the current value as target. A mode that
is not engaged writes nothing.

    AH:  vs_cmd    = clip(0.15 * alt_err, +-5 m/s)
         theta_cmd = clip(1.5 * (vs_cmd - vs), -8 deg, +12 deg)
         pitch     = clip(0.06 * (theta_cmd - pitch_deg), +-1)
    AP:  bank_cmd  = clip(0.8 * hdg_err, +-25 deg)
         roll      = clip(0.03 * (bank_cmd - roll_deg), +-1)
    AT:  throttle  = clip(0.5 + 0.04 * spd_err, 0, 1)

    hdg_err is the shorter arc, wrapped into [-180 deg, +180 deg).

"A mode that is not engaged writes nothing" is realized literally: `compute`
returns a mapping whose keys are only the axes the engaged modes drive. An
unengaged mode contributes no key at all -- not a zero, which the pilot's own
input would then be overwritten by.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# D11 gains and limits, transcribed.
# ---------------------------------------------------------------------------
AH_ALT_GAIN = 0.15          # vs_cmd = clip(0.15 * alt_err, +-5)
AH_VS_LIMIT_MPS = 5.0
AH_THETA_GAIN = 1.5         # theta_cmd = clip(1.5 * (vs_cmd - vs), -8, +12)
AH_THETA_MIN_DEG = -8.0
AH_THETA_MAX_DEG = 12.0
AH_PITCH_GAIN = 0.06        # pitch = clip(0.06 * (theta_cmd - pitch_deg), +-1)

AP_BANK_GAIN = 0.8          # bank_cmd = clip(0.8 * hdg_err, +-25)
AP_BANK_LIMIT_DEG = 25.0
AP_ROLL_GAIN = 0.03         # roll = clip(0.03 * (bank_cmd - roll_deg), +-1)

AT_THROTTLE_BIAS = 0.5      # throttle = clip(0.5 + 0.04 * spd_err, 0, 1)
AT_SPEED_GAIN = 0.04


def clip(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else (hi if value > hi else value)


def heading_error_deg(target_deg: float, current_deg: float) -> float:
    """[a: D11] "hdg_err is the shorter arc, wrapped into [-180, +180)."

    Note the interval D11 gives is half-open at +180, so an error of exactly
    180 degrees wraps to -180 and the aircraft turns left rather than
    oscillating.
    """
    err = (target_deg - current_deg + 180.0) % 360.0 - 180.0
    # Python's % returns a value in [0, 360), so err is already in
    # [-180, +180). The line below documents that boundary rather than
    # changing it.
    return err


@dataclass(frozen=True)
class AutopilotTargets:
    """The three targets of L2SYS-REQ-01.11, in the units it names."""

    altitude_m: float = 0.0
    speed_mps: float = 0.0
    heading_deg: float = 0.0


@dataclass(frozen=True)
class AutopilotModes:
    """Which modes are engaged. [a: L2SYS-REQ-01.11]"""

    ap: bool = False        # heading hold
    ah: bool = False        # altitude hold
    at: bool = False        # autothrottle

    @property
    def any_engaged(self) -> bool:
        return self.ap or self.ah or self.at


def altitude_hold(alt_err_m: float, vs_mps: float, pitch_deg: float) -> float:
    """AH. [a: D11]

        vs_cmd    = clip(0.15 * alt_err, +-5)
        theta_cmd = clip(1.5 * (vs_cmd - vs), -8, +12)
        pitch     = clip(0.06 * (theta_cmd - pitch_deg), +-1)
    """
    vs_cmd = clip(AH_ALT_GAIN * alt_err_m, -AH_VS_LIMIT_MPS, AH_VS_LIMIT_MPS)
    theta_cmd = clip(AH_THETA_GAIN * (vs_cmd - vs_mps),
                     AH_THETA_MIN_DEG, AH_THETA_MAX_DEG)
    return clip(AH_PITCH_GAIN * (theta_cmd - pitch_deg), -1.0, 1.0)


def heading_hold(hdg_err_deg: float, roll_deg: float) -> float:
    """AP. [a: D11]

        bank_cmd = clip(0.8 * hdg_err, +-25)
        roll     = clip(0.03 * (bank_cmd - roll_deg), +-1)
    """
    bank_cmd = clip(AP_BANK_GAIN * hdg_err_deg,
                    -AP_BANK_LIMIT_DEG, AP_BANK_LIMIT_DEG)
    return clip(AP_ROLL_GAIN * (bank_cmd - roll_deg), -1.0, 1.0)


def autothrottle(spd_err_mps: float) -> float:
    """AT. [a: D11] throttle = clip(0.5 + 0.04 * spd_err, 0, 1)"""
    return clip(AT_THROTTLE_BIAS + AT_SPEED_GAIN * spd_err_mps, 0.0, 1.0)


class Autopilot:
    """The three modes and their targets. [a: L2SYS-REQ-01.11] [a: D11]"""

    def __init__(self) -> None:
        self.targets = AutopilotTargets()
        self.modes = AutopilotModes()

    # -- engaging captures the current value as target [a: D11] ---------
    def engage_ah(self, current_alt_m: float) -> None:
        self.targets = AutopilotTargets(current_alt_m, self.targets.speed_mps,
                                        self.targets.heading_deg)
        self.modes = AutopilotModes(self.modes.ap, True, self.modes.at)

    def engage_ap(self, current_heading_deg: float) -> None:
        self.targets = AutopilotTargets(self.targets.altitude_m,
                                        self.targets.speed_mps,
                                        current_heading_deg % 360.0)
        self.modes = AutopilotModes(True, self.modes.ah, self.modes.at)

    def engage_at(self, current_speed_mps: float) -> None:
        self.targets = AutopilotTargets(self.targets.altitude_m,
                                        current_speed_mps,
                                        self.targets.heading_deg)
        self.modes = AutopilotModes(self.modes.ap, self.modes.ah, True)

    def disengage_ah(self) -> None:
        self.modes = AutopilotModes(self.modes.ap, False, self.modes.at)

    def disengage_ap(self) -> None:
        self.modes = AutopilotModes(False, self.modes.ah, self.modes.at)

    def disengage_at(self) -> None:
        self.modes = AutopilotModes(self.modes.ap, self.modes.ah, False)

    def disengage_all(self) -> None:
        """[a: D8] RESET "returns the clean state -- autopilot ...
        disengaged"."""
        self.modes = AutopilotModes(False, False, False)

    def set_targets(self, altitude_m: float | None = None,
                    speed_mps: float | None = None,
                    heading_deg: float | None = None) -> AutopilotTargets:
        self.targets = AutopilotTargets(
            self.targets.altitude_m if altitude_m is None else altitude_m,
            self.targets.speed_mps if speed_mps is None else speed_mps,
            (self.targets.heading_deg if heading_deg is None
             else heading_deg % 360.0),
        )
        return self.targets

    # -- the laws -------------------------------------------------------
    def compute(
        self,
        alt_m: float,
        vspeed_mps: float,
        pitch_deg: float,
        heading_deg: float,
        roll_deg: float,
        speed_mps: float,
    ) -> dict[str, float]:
        """The commands the engaged modes write. [a: D11]

        **A mode that is not engaged contributes no key**, so a caller that
        applies this mapping over the pilot's own commands leaves the axes the
        autopilot does not own untouched.
        """
        out: dict[str, float] = {}
        if self.modes.ah:
            out["pitch"] = altitude_hold(
                alt_err_m=self.targets.altitude_m - alt_m,
                vs_mps=vspeed_mps,
                pitch_deg=pitch_deg,
            )
        if self.modes.ap:
            out["roll"] = heading_hold(
                hdg_err_deg=heading_error_deg(self.targets.heading_deg,
                                              heading_deg),
                roll_deg=roll_deg,
            )
        if self.modes.at:
            out["throttle"] = autothrottle(
                spd_err_mps=self.targets.speed_mps - speed_mps
            )
        return out
