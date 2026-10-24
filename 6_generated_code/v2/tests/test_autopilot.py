"""Autopilot laws.

This module verifies L2SYS-REQ-01.11 throughout, on its law side: "engaging and
disengaging the AP (heading hold), AH (altitude hold) and AT (autothrottle)
modes". The panel side is in `test_pcs.py`.

Plan rows: P-01.11-3 .. P-01.11-8. P-01.11-5 as amended by A-3.

Every reference value is a hand computation of the D11 formula, written out in
full in the test body. Nothing is read back from the implementation. [a: R-2]
"""

from __future__ import annotations

import pytest

from flightsim.pcs import autopilot as ap
from flightsim.pcs.autopilot import Autopilot

# ---------------------------------------------------------------------------
# D11, transcribed.
#
#   AH:  vs_cmd    = clip(0.15 * alt_err, +-5 m/s)
#        theta_cmd = clip(1.5 * (vs_cmd - vs), -8 deg, +12 deg)
#        pitch     = clip(0.06 * (theta_cmd - pitch_deg), +-1)
#   AP:  bank_cmd  = clip(0.8 * hdg_err, +-25 deg)
#        roll      = clip(0.03 * (bank_cmd - roll_deg), +-1)
#   AT:  throttle  = clip(0.5 + 0.04 * spd_err, 0, 1)
#   hdg_err is the shorter arc, wrapped into [-180, +180).
# ---------------------------------------------------------------------------


def test_the_gains_are_the_ones_d11_states():
    """The constants themselves, against D11. [a: D11]"""
    assert ap.AH_ALT_GAIN == 0.15
    assert ap.AH_VS_LIMIT_MPS == 5.0
    assert ap.AH_THETA_GAIN == 1.5
    assert (ap.AH_THETA_MIN_DEG, ap.AH_THETA_MAX_DEG) == (-8.0, 12.0)
    assert ap.AH_PITCH_GAIN == 0.06
    assert ap.AP_BANK_GAIN == 0.8
    assert ap.AP_BANK_LIMIT_DEG == 25.0
    assert ap.AP_ROLL_GAIN == 0.03
    assert ap.AT_THROTTLE_BIAS == 0.5
    assert ap.AT_SPEED_GAIN == 0.04


# ===========================================================================
# P-01.11-4 -- AH, hand computed
# ===========================================================================
def test_ah_law_hand_computed():
    """P-01.11-4. [a: D11] AH with alt_err = +100 m, vs = 0, pitch_deg = 0.

    Hand computation, written out:
        vs_cmd    = clip(0.15 * 100, +-5)      = clip(15.0, +-5)   = 5.0
        theta_cmd = clip(1.5 * (5.0 - 0), -8, +12) = clip(7.5, ...) = 7.5
        pitch     = clip(0.06 * (7.5 - 0), +-1) = clip(0.45, +-1)  = 0.45
    """
    vs_cmd = min(5.0, max(-5.0, 0.15 * 100.0))
    assert vs_cmd == 5.0
    theta_cmd = min(12.0, max(-8.0, 1.5 * (vs_cmd - 0.0)))
    assert theta_cmd == 7.5
    pitch = min(1.0, max(-1.0, 0.06 * (theta_cmd - 0.0)))
    assert pitch == pytest.approx(0.45)

    assert ap.altitude_hold(alt_err_m=100.0, vs_mps=0.0,
                            pitch_deg=0.0) == pytest.approx(0.45)


def test_ah_law_below_the_vs_limit():
    """P-01.11-4. A small error stays inside every clip, so the row shows the
    unsaturated path too. [a: D11]

        alt_err = +10, vs = +1, pitch_deg = +2
        vs_cmd    = clip(0.15 * 10, +-5) = 1.5
        theta_cmd = clip(1.5 * (1.5 - 1.0), -8, +12) = clip(0.75, ...) = 0.75
        pitch     = clip(0.06 * (0.75 - 2.0), +-1) = 0.06 * -1.25 = -0.075
    """
    assert ap.altitude_hold(10.0, 1.0, 2.0) == pytest.approx(-0.075)


def test_ah_law_saturates():
    """P-01.11-5, **as amended by A-3**. [a: D11]

    The frozen row stated `theta_cmd = +12` for a stimulus that in fact gives
    7.5; A-3 corrects the intermediate and adds a stimulus that does reach the
    +12 clip. Both are computed longhand here.

    Case 1 (the frozen stimulus, corrected):
        alt_err = +10 000, vs = 0, pitch_deg = -90
        vs_cmd    = clip(0.15 * 10000, +-5)          = 5.0    (vs clip binds)
        theta_cmd = clip(1.5 * (5.0 - 0.0), -8, +12) = 7.5    (theta clip does
                                                               NOT bind)
        pitch     = clip(0.06 * (7.5 - (-90)), +-1)
                  = clip(0.06 * 97.5, +-1) = clip(5.85, +-1)  = +1.0

    Case 2 (added by A-3, so the +12 clip is exercised):
        alt_err = +10 000, vs = -3, pitch_deg = 0
        vs_cmd    = 5.0
        theta_cmd = clip(1.5 * (5.0 - (-3.0)), -8, +12)
                  = clip(1.5 * 8.0, -8, +12) = clip(12.0, ...) = 12.0  (binds)
        pitch     = clip(0.06 * (12.0 - 0.0), +-1) = 0.72
    """
    # -- case 1
    vs_cmd = min(5.0, max(-5.0, 0.15 * 10_000.0))
    assert vs_cmd == 5.0
    theta_cmd = min(12.0, max(-8.0, 1.5 * (vs_cmd - 0.0)))
    assert theta_cmd == 7.5, "the theta clip does not bind at vs = 0"
    assert ap.altitude_hold(10_000.0, 0.0, -90.0) == pytest.approx(1.0)

    # -- case 2, where the +12 clip does bind
    theta_cmd2 = min(12.0, max(-8.0, 1.5 * (5.0 - (-3.0))))
    assert theta_cmd2 == 12.0
    assert ap.altitude_hold(10_000.0, -3.0, 0.0) == pytest.approx(0.72)

    # -- and the lower theta clip, -8
    #    alt_err = -10 000 -> vs_cmd = -5; vs = +3 -> 1.5 * (-5 - 3) = -12,
    #    clipped to -8; pitch = clip(0.06 * (-8 - 0), +-1) = -0.48
    assert ap.altitude_hold(-10_000.0, 3.0, 0.0) == pytest.approx(-0.48)

    # -- and the pitch clip on the negative side
    assert ap.altitude_hold(-10_000.0, 0.0, 90.0) == pytest.approx(-1.0)


def test_ah_vs_limit_is_five_metres_per_second():
    """P-01.11-5. The +-5 m/s limit binds and is symmetric. [a: D11]"""
    # 0.15 * 40 = 6.0, above the limit -> 5.0
    assert ap.altitude_hold(40.0, 5.0, 0.0) == pytest.approx(0.0)
    # 0.15 * 33.3333 = 5.0, exactly at the limit -> same answer
    assert ap.altitude_hold(100.0 / 3.0, 5.0, 0.0) == pytest.approx(0.0)


# ===========================================================================
# P-01.11-6 -- AP, hand computed
# ===========================================================================
def test_ap_law_hand_computed():
    """P-01.11-6. [a: D11] AP with hdg_err = +20 deg, roll_deg = 0.

    Hand computation:
        bank_cmd = clip(0.8 * 20, +-25) = clip(16.0, +-25) = 16.0
        roll     = clip(0.03 * (16.0 - 0), +-1) = clip(0.48, +-1) = 0.48
    """
    bank_cmd = min(25.0, max(-25.0, 0.8 * 20.0))
    assert bank_cmd == 16.0
    roll = min(1.0, max(-1.0, 0.03 * (bank_cmd - 0.0)))
    assert roll == pytest.approx(0.48)

    assert ap.heading_hold(hdg_err_deg=20.0, roll_deg=0.0) == (
        pytest.approx(0.48))


def test_ap_bank_limit_binds_at_twenty_five_degrees():
    """P-01.11-6. [a: D11] bank_cmd = clip(0.8 * hdg_err, +-25).

        hdg_err = +90  -> 0.8 * 90 = 72, clipped to +25
        roll    = clip(0.03 * (25 - 0), +-1) = 0.75
        hdg_err = -90  -> clipped to -25; roll = -0.75
    """
    assert ap.heading_hold(90.0, 0.0) == pytest.approx(0.75)
    assert ap.heading_hold(-90.0, 0.0) == pytest.approx(-0.75)
    # already banked past the command: the law rolls back
    #   bank_cmd = 25; roll = clip(0.03 * (25 - 60), +-1) = -1.05 -> -1.0
    assert ap.heading_hold(90.0, 60.0) == pytest.approx(-1.0)


# ===========================================================================
# P-01.11-7 -- AT, hand computed
# ===========================================================================
def test_at_law_hand_computed():
    """P-01.11-7. [a: D11] throttle = clip(0.5 + 0.04 * spd_err, 0, 1).

    Hand computation:
        spd_err = +5   -> clip(0.5 + 0.20, 0, 1) = 0.70
        spd_err = -20  -> clip(0.5 - 0.80, 0, 1) = clip(-0.30, 0, 1) = 0.00
        spd_err =  0   -> 0.50
        spd_err = +50  -> clip(0.5 + 2.00, 0, 1) = 1.00
        spd_err = -12.5-> clip(0.5 - 0.50, 0, 1) = 0.00
    """
    assert ap.autothrottle(5.0) == pytest.approx(0.7)
    assert ap.autothrottle(-20.0) == pytest.approx(0.0)
    assert ap.autothrottle(0.0) == pytest.approx(0.5)
    assert ap.autothrottle(50.0) == pytest.approx(1.0)
    assert ap.autothrottle(-12.5) == pytest.approx(0.0)


# ===========================================================================
# P-01.11-8 -- the shorter arc
# ===========================================================================
def test_heading_error_takes_the_shorter_arc():
    """P-01.11-8. [a: D11] "hdg_err is the shorter arc, wrapped into
    [-180, +180)."
    """
    assert ap.heading_error_deg(10.0, 350.0) == pytest.approx(20.0)
    assert ap.heading_error_deg(350.0, 10.0) == pytest.approx(-20.0)
    assert ap.heading_error_deg(0.0, 0.0) == pytest.approx(0.0)
    assert ap.heading_error_deg(90.0, 0.0) == pytest.approx(90.0)
    assert ap.heading_error_deg(270.0, 0.0) == pytest.approx(-90.0)
    # the interval is half-open at +180, so exactly 180 wraps to -180
    assert ap.heading_error_deg(180.0, 0.0) == pytest.approx(-180.0)
    assert ap.heading_error_deg(0.0, 180.0) == pytest.approx(-180.0)


@pytest.mark.parametrize("target", [0.0, 45.5, 179.9, 180.0, 270.0, 359.9])
@pytest.mark.parametrize("current", [0.0, 90.0, 180.0, 270.0, 359.0])
def test_heading_error_is_always_inside_the_interval(target, current):
    """P-01.11-8. Every case lands in [-180, +180). [a: D11]"""
    err = ap.heading_error_deg(target, current)
    assert -180.0 <= err < 180.0


def test_heading_error_never_takes_the_long_way_round():
    """P-01.11-8. "the shorter arc": the magnitude is never more than the other
    way round. [a: D11]"""
    for target in range(0, 360, 7):
        for current in range(0, 360, 11):
            err = ap.heading_error_deg(float(target), float(current))
            the_long_way = 360.0 - abs(err)
            assert abs(err) <= the_long_way + 1e-9


# ===========================================================================
# P-01.11-3 -- a disengaged mode writes nothing
# ===========================================================================
def test_a_disengaged_mode_writes_nothing():
    """P-01.11-3. [a: D11] "A mode that is not engaged writes nothing."

    Not "writes zero" -- **nothing**. A zero would overwrite the pilot's own
    command on that axis; an absent key leaves it alone.
    """
    a = Autopilot()
    assert a.compute(alt_m=1000.0, vspeed_mps=0.0, pitch_deg=0.0,
                     heading_deg=0.0, roll_deg=0.0, speed_mps=100.0) == {}

    a.engage_ah(1000.0)
    out = a.compute(alt_m=900.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=0.0, roll_deg=0.0, speed_mps=100.0)
    assert set(out) == {"pitch"}, "AH alone must write only pitch"

    a.engage_ap(90.0)
    out = a.compute(alt_m=900.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=0.0, roll_deg=0.0, speed_mps=100.0)
    assert set(out) == {"pitch", "roll"}

    a.engage_at(150.0)
    out = a.compute(alt_m=900.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=0.0, roll_deg=0.0, speed_mps=100.0)
    assert set(out) == {"pitch", "roll", "throttle"}

    a.disengage_ah()
    out = a.compute(alt_m=900.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=0.0, roll_deg=0.0, speed_mps=100.0)
    assert set(out) == {"roll", "throttle"}
    assert "pitch" not in out


def test_the_computed_values_are_the_hand_computed_laws():
    """P-01.11-3..7. The mapping carries the same numbers the individual laws
    give, so `compute` is not a second implementation. [a: D11]
    """
    a = Autopilot()
    a.engage_ah(1100.0)          # target 1100, current 1000 -> alt_err +100
    a.engage_ap(20.0)            # target 20, current 0 -> hdg_err +20
    a.engage_at(105.0)           # target 105, current 100 -> spd_err +5
    out = a.compute(alt_m=1000.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=0.0, roll_deg=0.0, speed_mps=100.0)
    assert out["pitch"] == pytest.approx(0.45)
    assert out["roll"] == pytest.approx(0.48)
    assert out["throttle"] == pytest.approx(0.7)


# ===========================================================================
# Engagement captures the current value [a: D11]
# ===========================================================================
def test_engaging_captures_the_current_value_as_target():
    """P-01.11-2 (model side). [a: D11] "Engaging AP, AH or AT captures the
    current value as target."
    """
    a = Autopilot()
    a.engage_ah(2345.6)
    assert a.targets.altitude_m == pytest.approx(2345.6)
    a.engage_ap(123.4)
    assert a.targets.heading_deg == pytest.approx(123.4)
    a.engage_at(212.1)
    assert a.targets.speed_mps == pytest.approx(212.1)
    # engaging one does not disturb another's target
    assert a.targets.altitude_m == pytest.approx(2345.6)


def test_engaging_at_the_current_value_commands_no_correction():
    """P-01.11-2. Capturing the current value means the law is at rest the
    instant it engages, which is what makes engagement bumpless. [b: D11]
    """
    a = Autopilot()
    a.engage_ah(1000.0)
    out = a.compute(alt_m=1000.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=0.0, roll_deg=0.0, speed_mps=0.0)
    assert out["pitch"] == pytest.approx(0.0)

    a.engage_ap(90.0)
    out = a.compute(alt_m=1000.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=90.0, roll_deg=0.0, speed_mps=0.0)
    assert out["roll"] == pytest.approx(0.0)

    a.engage_at(150.0)
    out = a.compute(alt_m=1000.0, vspeed_mps=0.0, pitch_deg=0.0,
                    heading_deg=90.0, roll_deg=0.0, speed_mps=150.0)
    assert out["throttle"] == pytest.approx(0.5)


def test_heading_target_is_normalized_into_zero_to_360():
    """[b: D11] a captured heading of 370 deg is 10 deg."""
    a = Autopilot()
    a.engage_ap(370.0)
    assert a.targets.heading_deg == pytest.approx(10.0)


def test_disengage_all_clears_every_mode():
    """[a: D8] RESET "returns the clean state -- autopilot ... disengaged"."""
    a = Autopilot()
    a.engage_ah(100.0)
    a.engage_ap(100.0)
    a.engage_at(100.0)
    assert a.modes.any_engaged
    a.disengage_all()
    assert not a.modes.any_engaged
    assert a.compute(0, 0, 0, 0, 0, 0) == {}
