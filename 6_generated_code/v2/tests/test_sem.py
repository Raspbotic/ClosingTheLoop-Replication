"""Synthetic Environment Manager.

Plan rows: P-03.01-1, P-03.01-2, P-03.02-1 .. -3, P-03.03-1 .. -3,
P-03.04-1 .. -3, P-03.05-1 .. -5, C-D2-1, C-D2-3, C-D2-4.

The radar-equation reference values are computed **longhand in this file** from
the formula D12 states, with every intermediate written out. Nothing is read
back from the implementation. [a: R-2]

No external network access anywhere in this module. [a: R-7 acceptance]
"""

from __future__ import annotations

import math

import pytest

from flightsim.common import messages, ports
from flightsim.common.messages import RadarBeacon
from flightsim import sem_model
from flightsim.sem_model import (
    Beacon,
    EmitterParameters,
    Scenario,
    ScenarioPublisher,
)

from test_common_messages import a_state

# ---------------------------------------------------------------------------
# D12, transcribed.
#
#   R_max = (Pt * G^2 * lambda^2 * sigma / ((4 pi)^3 * P_min * L)) ^ (1/4)
#   lambda = c / f
#   defaults: Pt 100 kW, G 35 dBi, f 10 GHz, P_min -90 dBm, L 1.0
#   ownship RCS default 5 m^2, "giving about 39 km at the defaults"
# ---------------------------------------------------------------------------
D12_PT_W = 100_000.0
D12_GAIN_DBI = 35.0
D12_FREQ_HZ = 10e9
D12_PMIN_DBM = -90.0
D12_LOSS = 1.0
D12_RCS_M2 = 5.0
D12_STATED_RANGE_M = 39_000.0

C_MPS = 299_792_458.0


def hand_computed_rmax(pt_w=D12_PT_W, gain_dbi=D12_GAIN_DBI,
                       freq_hz=D12_FREQ_HZ, pmin_dbm=D12_PMIN_DBM,
                       rcs_m2=D12_RCS_M2, loss=D12_LOSS) -> float:
    """The D12 formula, written out longhand in the test file.

    Deliberately independent of `flightsim.sem_model`: it repeats the algebra
    from the Decision Annex rather than calling the implementation, so an error
    in the implementation cannot make the reference agree with it. [a: R-2]
    """
    g_linear = 10.0 ** (gain_dbi / 10.0)          # 35 dBi -> 3162.2776601...
    lam = C_MPS / freq_hz                         # 10 GHz -> 0.0299792458 m
    p_min_w = 1e-3 * (10.0 ** (pmin_dbm / 10.0))  # -90 dBm -> 1e-12 W
    four_pi_cubed = (4.0 * math.pi) ** 3          # 1984.4017075...
    numerator = pt_w * g_linear * g_linear * lam * lam * rcs_m2
    denominator = four_pi_cubed * p_min_w * loss
    return (numerator / denominator) ** 0.25


@pytest.fixture
def scenario():
    return Scenario()


def parameterized(beacon_id="EM1", classification="FOE", lat=45.5, lon=9.3,
                  **emitter):
    return Beacon(beacon_id=beacon_id, classification=classification,
                  lat=lat, lon=lon, alt=0.0,
                  emitter=EmitterParameters(**emitter))


def explicit(beacon_id="EX1", classification="FRIEND", range_m=5000.0,
             lat=45.5, lon=9.3):
    return Beacon(beacon_id=beacon_id, classification=classification,
                  lat=lat, lon=lon, alt=0.0, explicit_range_m=range_m,
                  emitter=None)


# ===========================================================================
# P-03.05-1 -- the radar equation, hand computed
# ===========================================================================
def test_radar_equation_hand_computed_at_the_defaults():
    """P-03.05-1. [a: L2SYS-REQ-03.05] [a: D12]

    Hand computation at the D12 defaults, every intermediate written out:

        G       = 10 ** (35 / 10)            = 3162.2776601683795
        lambda  = 299792458 / 1e10           = 0.0299792458 m
        P_min   = 1e-3 * 10 ** (-90 / 10)    = 1e-12 W
        (4 pi)^3                             = 1984.4017075391884
        numerator   = 1e5 * G^2 * lambda^2 * 5
        denominator = 1984.4017075391884 * 1e-12 * 1.0
        R_max       = (numerator / denominator) ** 0.25

    D12 states this is "about 39 km".
    """
    g = 10.0 ** (35.0 / 10.0)
    assert g == pytest.approx(3162.2776601683795)
    lam = C_MPS / 10e9
    assert lam == pytest.approx(0.0299792458)
    p_min = 1e-3 * (10.0 ** (-90.0 / 10.0))
    assert p_min == pytest.approx(1e-12)
    four_pi_cubed = (4.0 * math.pi) ** 3
    assert four_pi_cubed == pytest.approx(1984.4017075391884)

    numerator = 1e5 * g * g * lam * lam * 5.0
    denominator = four_pi_cubed * p_min * 1.0
    expected = (numerator / denominator) ** 0.25

    got = sem_model.max_detection_range_m()
    assert got == pytest.approx(expected, rel=1e-12)
    # and it is the figure D12 states, to within 5 %
    assert got == pytest.approx(D12_STATED_RANGE_M, rel=0.05), (
        f"{got:.0f} m against D12's stated ~39 km"
    )


def test_wavelength_is_c_over_f():
    """P-03.05-1. [a: D12] "lambda = c / f"."""
    assert sem_model.wavelength_m(10e9) == pytest.approx(0.0299792458)
    assert sem_model.wavelength_m(3e9) == pytest.approx(C_MPS / 3e9)


def test_unit_conversions_are_the_ones_d12_implies():
    """P-03.05-1. 35 dBi and -90 dBm are the defaults D12 gives, so the
    equation's G and P_min are the linear forms of those figures. [b: D12]
    """
    assert sem_model.dbi_to_linear(0.0) == pytest.approx(1.0)
    assert sem_model.dbi_to_linear(10.0) == pytest.approx(10.0)
    assert sem_model.dbi_to_linear(35.0) == pytest.approx(3162.2776601683795)
    assert sem_model.dbm_to_watts(0.0) == pytest.approx(1e-3)     # 0 dBm = 1 mW
    assert sem_model.dbm_to_watts(-90.0) == pytest.approx(1e-12)
    assert sem_model.dbm_to_watts(30.0) == pytest.approx(1.0)     # 30 dBm = 1 W


# ===========================================================================
# P-03.05-2 -- the scaling laws
# ===========================================================================
def test_radar_equation_scaling_laws():
    """P-03.05-2. [a: D12] The exponents follow from the fourth root:

        R ~ Pt^(1/4)      -- Pt x16   -> R x2   (16^(1/4) = 2)
        R ~ sigma^(1/4)   -- sigma x16-> R x2
        R ~ P_min^(-1/4)  -- P_min /16-> R x2
        R ~ L^(-1/4)      -- L x16    -> R /2
        R ~ G^(1/2)       -- G^2 under a fourth root; G x4 -> R x2
        R ~ lambda^(1/2) ~ f^(-1/2)   -- f x4    -> R /2
    """
    base = sem_model.max_detection_range_m()

    assert sem_model.max_detection_range_m(pt_w=D12_PT_W * 16) / base == (
        pytest.approx(2.0, rel=1e-9))
    assert sem_model.max_detection_range_m(rcs_m2=D12_RCS_M2 * 16) / base == (
        pytest.approx(2.0, rel=1e-9))
    # P_min / 16 is -90 dBm - 10*log10(16) = -90 - 12.0411998 dBm
    quieter = D12_PMIN_DBM - 10.0 * math.log10(16.0)
    assert sem_model.max_detection_range_m(pmin_dbm=quieter) / base == (
        pytest.approx(2.0, rel=1e-9))
    assert sem_model.max_detection_range_m(loss=16.0) / base == (
        pytest.approx(0.5, rel=1e-9))
    # G x4 is +10*log10(4) = +6.0205999 dB
    louder = D12_GAIN_DBI + 10.0 * math.log10(4.0)
    assert sem_model.max_detection_range_m(gain_dbi=louder) / base == (
        pytest.approx(2.0, rel=1e-9))
    assert sem_model.max_detection_range_m(frequency_hz=D12_FREQ_HZ * 4) / base == (
        pytest.approx(0.5, rel=1e-9))


@pytest.mark.parametrize("pt,gain,freq,pmin,rcs,loss", [
    (1e3, 20.0, 3e9, -80.0, 1.0, 2.0),
    (5e5, 40.0, 16e9, -110.0, 25.0, 1.5),
    (2e4, 12.5, 1.2e9, -75.5, 0.1, 3.3),
])
def test_radar_equation_at_sampled_points(pt, gain, freq, pmin, rcs, loss):
    """P-03.05-2. "the scaling laws are checked as well as sampled points."
    Each point is computed longhand in this file. [a: R-7] [a: D12]
    """
    expected = hand_computed_rmax(pt, gain, freq, pmin, rcs, loss)
    got = sem_model.max_detection_range_m(pt_w=pt, gain_dbi=gain,
                                          frequency_hz=freq, pmin_dbm=pmin,
                                          rcs_m2=rcs, loss=loss)
    assert got == pytest.approx(expected, rel=1e-12)


# ===========================================================================
# P-03.05-3 -- inclusive detection boundary
# ===========================================================================
def test_detection_is_inclusive_at_the_boundary():
    """P-03.05-3. [a: D12] "Detection = slant range <= R_max, inclusive.\""""
    r = sem_model.max_detection_range_m()
    assert sem_model.is_detected(r, r) is True             # exactly at R_max
    assert sem_model.is_detected(r - 1.0, r) is True
    assert sem_model.is_detected(r + 1.0, r) is False
    assert sem_model.is_detected(0.0, r) is True


# ===========================================================================
# P-03.05-4 -- slant range
# ===========================================================================
def test_slant_range_hand_computed():
    """P-03.05-4. Great-circle ground distance combined with the altitude
    difference by Pythagoras, computed longhand here. [a: fsim_TA_SEM.sysml]
    calcBeaconOwnshipRange.
    """
    R = 6_371_000.0
    # one degree of latitude at the same longitude
    ground = R * math.radians(1.0)          # 111194.9266 m
    assert ground == pytest.approx(111194.92664455873, rel=1e-9)

    got = sem_model.slant_range_m(45.0, 9.0, 0.0, 46.0, 9.0, 0.0)
    assert got == pytest.approx(ground, rel=1e-6)

    # with an altitude difference: hypot(ground, dalt)
    expected = math.hypot(ground, 10_000.0)
    got_alt = sem_model.slant_range_m(45.0, 9.0, 0.0, 46.0, 9.0, 10_000.0)
    assert got_alt == pytest.approx(expected, rel=1e-6)

    # co-located but 5000 m below: the slant range is exactly the altitude
    assert sem_model.slant_range_m(45.0, 9.0, 5000.0, 45.0, 9.0, 0.0) == (
        pytest.approx(5000.0, rel=1e-9))
    # and it is symmetric
    assert sem_model.slant_range_m(45.0, 9.0, 0.0, 46.0, 10.0, 0.0) == (
        pytest.approx(
            sem_model.slant_range_m(46.0, 10.0, 0.0, 45.0, 9.0, 0.0),
            rel=1e-9))


# ===========================================================================
# P-03.05-5 -- recomputation against the received ownship state
# ===========================================================================
def test_detection_recomputed_on_each_received_ownship_state(scenario):
    """P-03.05-5. [a: L2SYS-REQ-03.05] "by evaluating the monostatic radar
    equation against the received ownship state." Approaching a beacon crosses
    R_max once, and one publication accompanies the crossing.
    """
    beacon = parameterized(lat=46.0, lon=9.0)
    scenario.add(beacon)
    r_max = beacon.max_range_m(scenario.ownship_rcs_m2)

    # three approaches, the last inside R_max
    far = a_state(lat_deg=44.0, lon_deg=9.0, alt_m=0.0)      # ~222 km
    mid = a_state(lat_deg=45.3, lon_deg=9.0, alt_m=0.0)      # ~78 km
    near = a_state(lat_deg=45.9, lon_deg=9.0, alt_m=0.0)     # ~11 km

    assert sem_model.slant_range_m(44.0, 9.0, 0.0, 46.0, 9.0, 0.0) > r_max
    assert sem_model.slant_range_m(45.9, 9.0, 0.0, 46.0, 9.0, 0.0) < r_max

    scenario.published.clear()

    # The FIRST received state establishes each beacon's status and slant
    # range: one publication per beacon. [A-2]
    first = scenario.update_ownship(far)
    assert len(first) == 1
    assert first[0].detected is False
    assert first[0].slant_range_m == pytest.approx(222389.85, rel=1e-4)

    # Closing but still outside R_max: unchanged status, so nothing is
    # transmitted. [a: D2] "only the changed beacon is transmitted"
    assert scenario.update_ownship(mid) == []

    # Crossing R_max: exactly one publication, detected.
    crossed = scenario.update_ownship(near)
    assert len(crossed) == 1, "the R_max crossing should publish exactly once"
    assert crossed[0].detected is True
    assert crossed[0].beacon_id == "EM1"

    # Still inside: nothing further.
    assert scenario.update_ownship(near) == []

    # Receding across R_max publishes the loss of detection, once.
    lost = scenario.update_ownship(far)
    assert len(lost) == 1
    assert lost[0].detected is False


def test_slant_range_is_published(scenario):
    """P-03.05-4. `slant_range_m` reaches the wire. [a: D1]"""
    scenario.add(parameterized(lat=45.5, lon=9.3))
    scenario.update_ownship(a_state(lat_deg=45.4451, lon_deg=9.2767, alt_m=0.0))
    msg = scenario.to_message(scenario.beacons[0])
    expected = sem_model.slant_range_m(45.4451, 9.2767, 0.0, 45.5, 9.3, 0.0)
    assert msg.slant_range_m == pytest.approx(expected)


# ===========================================================================
# P-03.01 -- the Synthetic Environment exists and publishes
# ===========================================================================
def test_scenario_model_holds_and_publishes_a_roster(udp_sink):
    """P-03.01-1. [a: L2SYS-REQ-03.01] Two beacons added, two messages on the
    SEM's outbound port, each decoding to the beacon that was added. [a: D4]
    """
    sink = udp_sink(ports.SEM_TO_ORCH)
    publisher = ScenarioPublisher()
    try:
        scen = Scenario(publish=publisher)
        scen.add(parameterized("EM1", "FOE", lat=45.5, lon=9.3))
        scen.add(explicit("EM2", "FRIEND", range_m=8000.0))
        assert sink.wait_for(2)
        got = [messages.decode(d) for d in sink.datagrams[:2]]
        assert all(isinstance(m, RadarBeacon) for m in got)
        assert {m.beacon_id for m in got} == {"EM1", "EM2"}
        assert {m.classification for m in got} == {"FOE", "FRIEND"}
    finally:
        publisher.close()


def test_model_layer_imports_without_qt():
    """P-03.01-2. [a: R-7] "Keep the model layer of both free of Qt."

    Asserted on the source rather than on `sys.modules`, because the suite's
    own `qapp` fixture may have imported Qt for an unrelated row.
    """
    import ast
    import inspect

    for module in (sem_model, __import__("flightsim.pcs.slippy",
                                         fromlist=["slippy"])):
        tree = ast.parse(inspect.getsource(module))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        assert not any("Qt" in n or "qt" in n.split(".")[0].lower()
                       for n in names), (module.__name__, names)


# ===========================================================================
# P-03.02 -- spawn and classify
# ===========================================================================
@pytest.mark.parametrize("classification", ["FRIEND", "FOE", "UNKNOWN"])
def test_spawn_each_classification(classification, scenario):
    """P-03.02-1. [a: L2SYS-REQ-03.02] spawn and classify FRIEND, FOE,
    UNKNOWN."""
    msg = scenario.add(parameterized("EM1", classification))
    assert msg.classification == classification
    assert messages.decode(messages.encode(msg)).classification == classification


def test_an_unknown_classification_is_refused(scenario):
    """P-03.02-1. Only D1's three classifications exist. [a: D1]"""
    with pytest.raises(ValueError):
        Beacon(beacon_id="X", classification="HOSTILE", lat=0.0, lon=0.0)


def test_beacon_without_emitter_parameters_keeps_its_explicit_range(scenario):
    """P-03.02-2. [a: D12] "Beacons without emitter parameters keep their
    explicit range". [a: L2SYS-REQ-03.02] "with defined maximum ranges".
    """
    msg = scenario.add(explicit("EX1", "FRIEND", range_m=12345.0))
    assert msg.max_range_m == pytest.approx(12345.0)


def test_roster_reflects_insertions_and_removals(scenario):
    """P-03.02-3. [a: D2] "Every display of the beacon roster reflects both its
    insertions and its removals.\""""
    scenario.add(parameterized("EM1"))
    scenario.add(parameterized("EM2"))
    scenario.add(parameterized("EM3"))
    assert [b.beacon_id for b in scenario.beacons] == ["EM1", "EM2", "EM3"]

    scenario.remove("EM2")
    assert [b.beacon_id for b in scenario.beacons] == ["EM1", "EM3"]
    assert scenario.get("EM2") is None


# ===========================================================================
# P-03.03 -- per-beacon emitter parameters
# ===========================================================================
def test_emitter_parameter_defaults():
    """P-03.03-1. [a: D12] "defaults 100 kW, 35 dBi, 10 GHz, -90 dBm ... and L
    (default 1.0)". [a: L2SYS-REQ-03.03]
    """
    e = EmitterParameters()
    assert e.pt_w == D12_PT_W == 100_000.0
    assert e.gain_dbi == D12_GAIN_DBI == 35.0
    assert e.frequency_hz == D12_FREQ_HZ == 10e9
    assert e.pmin_dbm == D12_PMIN_DBM == -90.0
    assert e.loss == D12_LOSS == 1.0


@pytest.mark.parametrize("field,value,direction", [
    ("pt_w", D12_PT_W * 10, "up"),
    ("pt_w", D12_PT_W / 10, "down"),
    ("gain_dbi", D12_GAIN_DBI + 10, "up"),
    ("gain_dbi", D12_GAIN_DBI - 10, "down"),
    ("frequency_hz", D12_FREQ_HZ * 10, "down"),     # higher f, shorter range
    ("frequency_hz", D12_FREQ_HZ / 10, "up"),
    ("pmin_dbm", D12_PMIN_DBM - 10, "up"),          # more sensitive
    ("pmin_dbm", D12_PMIN_DBM + 10, "down"),
    ("loss", 10.0, "down"),
])
def test_each_emitter_parameter_changes_the_published_range(
    scenario, field, value, direction
):
    """P-03.03-2. [a: L2SYS-REQ-03.03] Each of Pt, G, f, P_min and L moves the
    published max_range_m, in the direction the D12 formula predicts.
    """
    scenario.add(parameterized("EM1"))
    before = scenario.to_message(scenario.beacons[0]).max_range_m
    after = scenario.edit("EM1", **{field: value}).max_range_m
    if direction == "up":
        assert after > before, (field, before, after)
    else:
        assert after < before, (field, before, after)
    # and the new figure is the hand computation with that one parameter moved
    kwargs = dict(pt_w=D12_PT_W, gain_dbi=D12_GAIN_DBI, freq_hz=D12_FREQ_HZ,
                  pmin_dbm=D12_PMIN_DBM, rcs_m2=D12_RCS_M2, loss=D12_LOSS)
    kwargs[{"frequency_hz": "freq_hz"}.get(field, field)] = value
    assert after == pytest.approx(hand_computed_rmax(**kwargs), rel=1e-12)


def test_wavelength_is_displayed_derived_from_frequency():
    """P-03.03-3. [a: D12] "Frequency is the editable parameter; wavelength is
    displayed derived."

    Hand computation: c/1e10 = 0.0299792458 m; c/3e9 = 0.09993081933... m.
    """
    assert EmitterParameters(frequency_hz=10e9).wavelength_m == (
        pytest.approx(0.0299792458))
    assert EmitterParameters(frequency_hz=3e9).wavelength_m == (
        pytest.approx(0.09993081933333333))


# ===========================================================================
# P-03.04 -- ownship RCS as a scenario parameter
# ===========================================================================
def test_ownship_rcs_default_is_five(scenario):
    """P-03.04-1. [a: D12] "Ownship RCS is a scenario spinbox, default 5 m^2".
    [a: L2SYS-REQ-03.04]
    """
    assert scenario.ownship_rcs_m2 == D12_RCS_M2 == 5.0
    assert sem_model.DEFAULT_OWNSHIP_RCS_M2 == 5.0


def test_rcs_change_recomputes_and_retransmits_every_parameterized_beacon(
    scenario
):
    """P-03.04-2. [a: D12] "a change of RCS recomputes and retransmits every
    beacon." [a: L2SYS-REQ-03.04]
    """
    for i in range(3):
        scenario.add(parameterized(f"EM{i + 1}", lat=45.5 + i * 0.01))
    before = {b.beacon_id: scenario.to_message(b).max_range_m
              for b in scenario.beacons}

    scenario.published.clear()
    republished = scenario.set_ownship_rcs(80.0)          # 5 -> 80 is x16

    assert len(republished) == 3, "every parameterized beacon must be resent"
    assert {m.beacon_id for m in republished} == {"EM1", "EM2", "EM3"}
    for msg in republished:
        # sigma x16 under a fourth root is x2
        assert msg.max_range_m == pytest.approx(2.0 * before[msg.beacon_id],
                                                rel=1e-9)


def test_rcs_change_does_not_recompute_explicit_range_beacons(scenario):
    """P-03.04-3. [a: D12] "Beacons without emitter parameters keep their
    explicit range and are not recomputed on an RCS change."
    """
    scenario.add(explicit("EX1", "FRIEND", range_m=7000.0))
    scenario.add(explicit("EX2", "UNKNOWN", range_m=9000.0))
    scenario.add(parameterized("EM1"))

    republished = scenario.set_ownship_rcs(80.0)
    assert {m.beacon_id for m in republished} == {"EM1"}, (
        "an explicit-range beacon was retransmitted on an RCS change"
    )
    assert scenario.to_message(scenario.get("EX1")).max_range_m == (
        pytest.approx(7000.0))
    assert scenario.to_message(scenario.get("EX2")).max_range_m == (
        pytest.approx(9000.0))


def test_rcs_must_be_positive(scenario):
    """P-03.04-1. A non-positive RCS has no physical meaning and would make the
    equation degenerate. [b: D12]"""
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError):
            scenario.set_ownship_rcs(bad)


# ===========================================================================
# C-D2 -- the beacon protocol
# ===========================================================================
def test_only_the_changed_beacon_is_transmitted(scenario):
    """C-D2-1. [a: D2] "Event-driven: only the changed beacon is transmitted.\""""
    for i in range(3):
        scenario.add(parameterized(f"EM{i + 1}", lat=45.5 + i * 0.01))
    scenario.published.clear()

    scenario.edit("EM2", pt_w=D12_PT_W * 4)

    assert len(scenario.published) == 1, scenario.published
    assert scenario.published[0].beacon_id == "EM2"


def test_deletion_publishes_a_tombstone(scenario):
    """C-D2-4. [a: D2] "Deletion is a tombstone (active=False).\""""
    scenario.add(parameterized("EM1"))
    scenario.published.clear()

    msg = scenario.remove("EM1")

    assert len(scenario.published) == 1
    assert msg.active is False
    assert msg.beacon_id == "EM1"
    assert msg.detected is False
    # and it survives the wire as a well-formed message
    assert messages.decode(messages.encode(msg)) == msg


def test_background_refresh_period_measured(clock):
    """C-D2-3. [a: D2] "Background refresh every 2.0 s." Measured against a
    clock the row controls: 10 s of clock, 5 refreshes.
    """
    timer = sem_model.RefreshTimer(clock=clock.monotonic)
    assert timer.period_s == messages.BEACON_REFRESH_S == 2.0

    fires = 0
    for _ in range(1050):                       # 10.5 s in 10 ms ticks
        clock.advance(0.01)
        if timer.due():
            fires += 1
    elapsed = clock.time()
    # 10.5 s at one refresh every 2.0 s -> fires at 2, 4, 6, 8, 10 = 5.
    # The window is deliberately 10.5 s rather than 10.0 s so the fifth fire is
    # not sitting exactly on the boundary, where binary accumulation of 0.01
    # decides the count.
    assert fires == 5, f"{fires} refreshes in {elapsed:.3f} s of clock"
    assert fires / elapsed == pytest.approx(0.5, abs=0.05)


def test_refresh_all_publishes_the_whole_roster(scenario):
    """C-D2-3. The periodic refresh is the whole roster, so a receiver that
    missed an event converges. [a: D2]"""
    for i in range(3):
        scenario.add(parameterized(f"EM{i + 1}", lat=45.5 + i * 0.01))
    scenario.published.clear()
    out = scenario.refresh_all()
    assert {m.beacon_id for m in out} == {"EM1", "EM2", "EM3"}


# ===========================================================================
# The widget, offscreen [a: D15]
# ===========================================================================
def test_sem_window_has_the_screenshot_controls(qt_widgets):
    """P-03.02-1 / P-03.03-1 / P-03.04-1, widget side. The controls the
    reference screenshot shows, driven offscreen. [a: Sec.4.3 layout] [a: D15]
    """
    from flightsim.run_sem import build_sem_window

    scen = Scenario()
    w = qt_widgets(build_sem_window(scenario=scen))

    assert w.findChild(type(w.connect_button), "connectToSim") is not None
    assert "Disconnected" in w.status_label.text()
    assert w.findChild(type(w.type_combo), "beaconType") is not None
    assert w.findChild(type(w.add_button), "addBeacon") is not None
    assert w.findChild(type(w.delete_button), "deleteBeacon") is not None
    assert w.findChild(type(w.roster), "activeEmitterRoster") is not None
    # every D1 classification is offerable
    offered = {w.type_combo.itemData(i) for i in range(w.type_combo.count())}
    assert offered == set(messages.CLASSIFICATIONS)


def test_sem_widget_spawns_and_deletes_through_the_real_buttons(qt_widgets):
    """P-03.02-1 / P-03.02-3, widget side. The requirement names a GUI, so the
    row drives the real buttons offscreen, not an internal API. [a: R-8 rule,
    applied here too] [a: D15]
    """
    from flightsim.run_sem import build_sem_window

    scen = Scenario()
    w = qt_widgets(build_sem_window(scenario=scen))

    w.type_combo.setCurrentIndex(
        [w.type_combo.itemData(i) for i in range(w.type_combo.count())]
        .index("FOE")
    )
    w.add_button.click()
    w.add_button.click()

    assert len(scen.beacons) == 2
    assert all(b.classification == "FOE" for b in scen.beacons)
    assert w.roster.count() == 2                      # the display reflects it

    w.index_spin.setValue(1)
    w.delete_button.click()
    assert len(scen.beacons) == 1
    assert w.roster.count() == 1                      # and the removal


def test_sem_widget_rcs_spinbox_is_a_scenario_control(qt_widgets):
    """P-03.04-1 / P-03.04-2, widget side. [a: D12] "Ownship RCS is a scenario
    spinbox" -- one control for the scenario, not one per beacon. Reported as
    F-5: the reference screenshot puts RCS in the per-beacon group.
    """
    from flightsim.run_sem import build_sem_window

    scen = Scenario()
    w = qt_widgets(build_sem_window(scenario=scen))

    assert w.rcs_spin.value() == pytest.approx(5.0)
    w.add_button.click()
    before = scen.to_message(scen.beacons[0]).max_range_m

    w.rcs_spin.setValue(80.0)                         # x16
    assert scen.ownship_rcs_m2 == pytest.approx(80.0)
    after = scen.to_message(scen.beacons[0]).max_range_m
    assert after == pytest.approx(2.0 * before, rel=1e-9)


def test_sem_widget_shows_wavelength_derived_from_frequency(qt_widgets):
    """P-03.03-3, widget side. [a: D12] frequency editable, wavelength derived.
    Reported as F-5: the screenshot has it the other way round.
    """
    from flightsim.run_sem import build_sem_window

    w = qt_widgets(build_sem_window(scenario=Scenario()))
    w.freq_spin.setValue(10e9)
    assert "0.029979" in w.wavelength_label.text()
    w.freq_spin.setValue(3e9)
    # c/3e9 = 0.09993081933... m, shown to six decimals as 0.099931
    assert "0.099931" in w.wavelength_label.text()
    # and there is no editable wavelength control
    assert not hasattr(w, "wavelength_spin")
