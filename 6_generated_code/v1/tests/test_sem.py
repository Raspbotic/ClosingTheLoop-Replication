"""
tests/test_sem.py
=================
Verification-plan rows allocated to the Synthetic Environment Manager:
L2SYS-REQ-03.01 - 03.05 plus the D2 and D12 conformance rows.

INDEPENDENCE RULE (R-2, restated by R-7 for 03.05): every reference value
below is computed inside the test from the formula stated in the
requirement / Decision Annex, never read back from the implementation.
"""

from __future__ import annotations

import math

import pytest

from flightsim.common import ports
from flightsim.common.messages import (
    BEACON_REFRESH_S, CLASSIFICATIONS, OwnshipState, RadarBeacon,
)
from flightsim import run_sem
from flightsim.run_sem import (
    DEFAULT_GAIN_DB, DEFAULT_FREQ_HZ, DEFAULT_OWNSHIP_RCS_M2, DEFAULT_PMIN_W,
    DEFAULT_PT_W, SemCore, calc_radar_equation, slant_range_m,
)

C = 299_792_458.0


# --------------------------------------------------------------------------
# Independent reference implementation of the D12 formula, written from the
# text of L2SYS-REQ-03.05 / D12 only.
# --------------------------------------------------------------------------

def reference_rmax(pt_w, gain_db, freq_hz, rcs_m2, pmin_w):
    """R_max = (Pt * G^2 * lambda^2 * sigma / ((4 pi)^3 P_min))^(1/4)
    [a: D12]. G is entered in dBi, so G_linear = 10^(dB/10);
    lambda = c / f."""
    g = 10.0 ** (gain_db / 10.0)
    lam = C / freq_hz
    return ((pt_w * g * g * lam * lam * rcs_m2)
            / (((4.0 * math.pi) ** 3) * pmin_w)) ** 0.25


@pytest.fixture
def sem():
    core = SemCore()
    yield core
    core.close()


@pytest.fixture
def sem_window(qapp):
    """The real SEM GUI, offscreen. Requirements 03.02-03.04 name the GUI,
    so those tests drive the actual widgets."""
    win = run_sem.build_window()
    yield win
    win.close()


# ==========================================================================
# L2SYS-REQ-03.01
# ==========================================================================

def test_03_01_synthetic_environment_exists_and_connects(sem):
    """L2SYS-REQ-03.01: the system shall comprise a Synthetic Environment
    for the simulation of external entities in the simulated scenario.

    Pass: the SEM exists, binds its D4 ownship-state input port and
    reports connected status after connectToSim.
    """
    assert sem.connected is False
    assert sem.connect_to_sim() is True
    assert sem.connected is True
    assert sem._rx.getsockname()[1] == ports.UDP_ORCH_TO_SEM == 5007
    assert sem.tx_port == ports.UDP_SEM_TO_ORCH == 6007


def test_03_01_gui_connect_button_reports_status(sem_window):
    """L2SYS-REQ-03.01 through the real GUI: CONNECT TO SIM changes the
    federation status readout (reference screenshot: 'Status:
    Disconnected')."""
    assert "Disconnected" in sem_window.status_label.text()
    sem_window.connect_button.click()
    assert "Connected" in sem_window.status_label.text()
    assert sem_window.core.connected is True


def test_03_01_receives_ownship_state_from_the_bus(sem, udp_sender):
    """L2SYS-REQ-03.01 / 03.05: the SEM consumes the ownship state
    arriving on the D4 orch->SEM port."""
    sem.connect_to_sim()
    state = OwnshipState(lat_deg=45.50, lon_deg=9.30, alt_m=3000.0)
    udp_sender.sendto(state.to_json(), (ports.LOCALHOST, sem.rx_port))
    got = None
    for _ in range(100):
        got = sem.poll_ownship()
        if got is not None:
            break
        import time
        time.sleep(0.01)
    assert got is not None
    assert sem.ownship.lat_deg == pytest.approx(45.50)


# ==========================================================================
# L2SYS-REQ-03.02
# ==========================================================================

@pytest.mark.parametrize("classification", list(CLASSIFICATIONS))
def test_03_02_spawn_and_classify_beacons_with_defined_range(
        sem, udp_receiver, classification):
    """L2SYS-REQ-03.02: the Synthetic Environment shall provide an
    interface (GUI) to spawn and classify (FRIEND, FOE, UNKNOWN) radar
    emitters with defined maximum ranges.

    Observation: the transmitted RadarBeacon on the D4 SEM->orch port.
    """
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    rec = sem.add_beacon(classification=classification, range_m=8000.0,
                         bearing_deg=90.0)
    assert rx.wait_for(1), "spawning a beacon transmitted nothing"
    msg = RadarBeacon.from_json(rx.datagrams[0])
    assert msg.classification == classification
    assert msg.beacon_id == rec.beacon_id
    assert msg.max_range_m > 0.0
    assert msg.active is True


def test_03_02_gui_add_beacon_button_spawns(sem_window, udp_receiver):
    """L2SYS-REQ-03.02 through the real GUI widgets (the requirement names
    the interface): Type combo + ADD BEACON TO GROUND."""
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    sem_window.type_combo.setCurrentText("FOE")
    sem_window.range_spin.setValue(12000.0)
    sem_window.theta_spin.setValue(270.0)
    sem_window.add_button.click()
    assert rx.wait_for(1)
    msg = RadarBeacon.from_json(rx.datagrams[0])
    assert msg.classification == "FOE"
    assert sem_window.roster.count() == 1


def test_03_02_beacon_with_explicit_range_keeps_it(sem):
    """L2SYS-REQ-03.02 "with defined maximum ranges"; [a: D12] "beacons
    without emitter params keep their explicit range"."""
    rec = sem.add_beacon(classification="FRIEND", range_m=5000.0,
                         explicit_range_m=25000.0)
    assert rec.has_emitter_params is False
    assert rec.max_range_m(sem.ownship_rcs_m2) == pytest.approx(25000.0)
    sem.set_ownship_rcs(80.0)                  # would move a computed range
    assert rec.max_range_m(sem.ownship_rcs_m2) == pytest.approx(25000.0)


def test_03_02_remove_beacon_emits_a_tombstone(sem, udp_receiver):
    """L2SYS-REQ-03.02 (dynamic removal, cf. req_scenarioManagement);
    [a: D2] "deletion via tombstone (active=False)"."""
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    rec = sem.add_beacon(classification="FOE", range_m=6000.0)
    assert rx.wait_for(1)
    rx.clear()
    tomb = sem.remove_beacon(rec.beacon_id)
    assert rx.wait_for(1)
    got = RadarBeacon.from_json(rx.datagrams[0])
    assert got.beacon_id == rec.beacon_id
    assert got.active is False and got.is_tombstone is True
    assert tomb.is_tombstone is True
    assert rec.beacon_id not in sem.beacons


def test_03_02_gui_delete_button_removes_the_selected_index(sem_window,
                                                            udp_receiver):
    """L2SYS-REQ-03.02 through the real GUI: Index spinbox +
    DELETE BEACON."""
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    sem_window.add_button.click()
    bid = next(iter(sem_window.core.beacons))
    assert rx.wait_for(1)          # let the spawn datagram land first
    rx.clear()
    sem_window.index_spin.setValue(bid)
    sem_window.delete_button.click()
    assert rx.wait_for(1)
    assert RadarBeacon.from_json(rx.datagrams[0]).active is False
    assert sem_window.roster.count() == 0


def test_03_02_invalid_classification_is_rejected(sem):
    """L2SYS-REQ-03.02 fixes the classification set to three values."""
    with pytest.raises(ValueError):
        sem.add_beacon(classification="HOSTILE")


# ==========================================================================
# L2SYS-REQ-03.03
# ==========================================================================

def test_03_03_per_beacon_emitter_parameters_are_editable(sem):
    """L2SYS-REQ-03.03: the GUI shall allow the instructor to set and
    adjust, FOR EACH BEACON, the emitter parameters of the radar equation
    (transmit power, antenna gain, frequency, receiver sensitivity).

    Pass: all four are settable per beacon and act independently.
    """
    a = sem.add_beacon(classification="FOE", range_m=1000.0)
    b = sem.add_beacon(classification="FRIEND", range_m=1000.0)
    sem.update_beacon(a.beacon_id, pt_w=400_000.0, gain_db=40.0,
                      freq_hz=3.0e9, pmin_w=1.0e-13)
    assert a.pt_w == 400_000.0 and a.gain_db == 40.0
    assert a.freq_hz == 3.0e9 and a.pmin_w == 1.0e-13
    # the second beacon keeps the defaults -> the parameters are per beacon
    assert b.pt_w == DEFAULT_PT_W and b.gain_db == DEFAULT_GAIN_DB
    assert b.freq_hz == DEFAULT_FREQ_HZ and b.pmin_w == DEFAULT_PMIN_W
    assert a.max_range_m(sem.ownship_rcs_m2) != \
        b.max_range_m(sem.ownship_rcs_m2)


def test_03_03_each_parameter_moves_rmax_in_the_expected_direction(sem):
    """L2SYS-REQ-03.03 + 03.05: each of the four parameters enters the
    radar equation, verified against an independent evaluation."""
    base = reference_rmax(DEFAULT_PT_W, DEFAULT_GAIN_DB, DEFAULT_FREQ_HZ,
                          DEFAULT_OWNSHIP_RCS_M2, DEFAULT_PMIN_W)
    rec = sem.add_beacon(classification="FOE", range_m=1000.0)
    assert rec.max_range_m(DEFAULT_OWNSHIP_RCS_M2) == pytest.approx(base,
                                                                    rel=1e-9)
    sem.update_beacon(rec.beacon_id, pt_w=DEFAULT_PT_W * 16.0)
    assert rec.max_range_m(DEFAULT_OWNSHIP_RCS_M2) == pytest.approx(
        base * 2.0, rel=1e-9)                 # 16x power -> 2x range
    sem.update_beacon(rec.beacon_id, pt_w=DEFAULT_PT_W,
                      pmin_w=DEFAULT_PMIN_W / 16.0)
    assert rec.max_range_m(DEFAULT_OWNSHIP_RCS_M2) == pytest.approx(
        base * 2.0, rel=1e-9)                 # 16x sensitivity -> 2x range


def test_03_03_gui_exposes_the_four_emitter_parameters(sem_window):
    """L2SYS-REQ-03.03 names the GUI, so the widgets must exist."""
    for widget in (sem_window.pt_spin, sem_window.gain_spin,
                   sem_window.freq_spin, sem_window.pmin_spin):
        assert widget is not None and widget.isEnabled()
    sem_window.pt_spin.setValue(250_000.0)
    sem_window.add_button.click()
    rec = next(iter(sem_window.core.beacons.values()))
    assert rec.pt_w == pytest.approx(250_000.0)


def test_03_03_gui_shows_live_computed_rmax(sem_window):
    """L2SYS-REQ-03.03 / L2SYS-REQ-03.05 in the GUI: [a: R-7] "live
    computed R_max"; the expected value is evaluated here from the
    [a: D12] formula, not read from the implementation."""
    text_before = sem_window.rmax_label.text()
    sem_window.pt_spin.setValue(DEFAULT_PT_W * 16.0)
    sem_window._update_rmax()
    assert sem_window.rmax_label.text() != text_before
    km = float(sem_window.rmax_label.text().split()[0])
    expected = reference_rmax(DEFAULT_PT_W * 16.0, DEFAULT_GAIN_DB,
                              DEFAULT_FREQ_HZ, DEFAULT_OWNSHIP_RCS_M2,
                              DEFAULT_PMIN_W) / 1000.0
    assert km == pytest.approx(expected, rel=1e-3)


# ==========================================================================
# L2SYS-REQ-03.04
# ==========================================================================

def test_03_04_ownship_rcs_is_a_scenario_parameter(sem):
    """L2SYS-REQ-03.04: the GUI shall allow the instructor to set and
    adjust the ownship radar cross section AS A SCENARIO PARAMETER.

    Pass: a single scenario-level value (not per beacon) that is
    adjustable and that every beacon's range depends on.
    """
    assert sem.ownship_rcs_m2 == DEFAULT_OWNSHIP_RCS_M2
    a = sem.add_beacon(classification="FOE", range_m=1000.0)
    b = sem.add_beacon(classification="FRIEND", range_m=1000.0)
    r_a0 = a.max_range_m(sem.ownship_rcs_m2)
    r_b0 = b.max_range_m(sem.ownship_rcs_m2)
    sem.set_ownship_rcs(16.0 * DEFAULT_OWNSHIP_RCS_M2)
    assert sem.ownship_rcs_m2 == 16.0 * DEFAULT_OWNSHIP_RCS_M2
    assert a.max_range_m(sem.ownship_rcs_m2) == pytest.approx(2.0 * r_a0,
                                                              rel=1e-9)
    assert b.max_range_m(sem.ownship_rcs_m2) == pytest.approx(2.0 * r_b0,
                                                              rel=1e-9)


def test_03_04_rcs_change_retransmits_every_beacon(sem, udp_receiver):
    """[a: D12] "an RCS change recomputes and retransmits every beacon"."""
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    ids = [sem.add_beacon(classification="FOE", range_m=1000.0).beacon_id
           for _ in range(3)]
    assert rx.wait_for(3)
    rx.clear()
    sem.set_ownship_rcs(20.0)
    assert rx.wait_for(3), "RCS change did not retransmit every beacon"
    got = {RadarBeacon.from_json(d).beacon_id for d in rx.datagrams}
    assert got == set(ids)


def test_03_04_gui_rcs_spinbox_drives_the_scenario(sem_window, udp_receiver):
    """L2SYS-REQ-03.04 names the GUI; [a: D12] "ownship RCS as scenario
    spinbox (default 5 m2)"."""
    assert sem_window.rcs_spin.value() == pytest.approx(
        DEFAULT_OWNSHIP_RCS_M2)
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    sem_window.add_button.click()
    rx.clear()
    sem_window.rcs_spin.setValue(50.0)
    assert sem_window.core.ownship_rcs_m2 == pytest.approx(50.0)
    assert rx.wait_for(1)


# ==========================================================================
# L2SYS-REQ-03.05 + D12
# ==========================================================================

def test_03_05_radar_equation_matches_an_independent_hand_computation():
    """L2SYS-REQ-03.05: evaluate the monostatic radar equation.
    [a: D12] R_max = (Pt G^2 lambda^2 sigma / ((4 pi)^3 P_min))^(1/4).

    The reference is evaluated in this test from the formula text, never
    read from the implementation (R-2/R-7 independence rule).
    """
    cases = [
        (DEFAULT_PT_W, DEFAULT_GAIN_DB, DEFAULT_FREQ_HZ,
         DEFAULT_OWNSHIP_RCS_M2, DEFAULT_PMIN_W),
        (50_000.0, 30.0, 3.0e9, 1.0, 1.0e-13),
        (1_000_000.0, 42.0, 16.0e9, 0.05, 5.0e-12),
    ]
    for pt, g, f, sigma, pmin in cases:
        assert calc_radar_equation(pt, g, f, sigma, pmin) == pytest.approx(
            reference_rmax(pt, g, f, sigma, pmin), rel=1e-9)


def test_d12_defaults_give_about_39_km():
    """[a: D12] "defaults 100 kW / 35 dBi / 10 GHz / -90 dBm, ownship RCS
    ... default 5 m2 -> approx 39 km"."""
    r = calc_radar_equation(DEFAULT_PT_W, DEFAULT_GAIN_DB, DEFAULT_FREQ_HZ,
                            DEFAULT_OWNSHIP_RCS_M2, DEFAULT_PMIN_W)
    expected = reference_rmax(DEFAULT_PT_W, DEFAULT_GAIN_DB, DEFAULT_FREQ_HZ,
                              DEFAULT_OWNSHIP_RCS_M2, DEFAULT_PMIN_W)
    assert r == pytest.approx(expected, rel=1e-9)
    assert 37_000.0 <= r <= 41_000.0, f"R_max {r/1000:.1f} km is not ~39 km"


def test_d12_defaults_are_the_annex_values():
    """[a: D12] the four defaults verbatim (-90 dBm = 1e-12 W)."""
    assert DEFAULT_PT_W == 100_000.0
    assert DEFAULT_GAIN_DB == 35.0
    assert DEFAULT_FREQ_HZ == 10.0e9
    assert DEFAULT_PMIN_W == pytest.approx(10.0 ** ((-90.0 - 30.0) / 10.0))
    assert DEFAULT_OWNSHIP_RCS_M2 == 5.0


def test_03_05_fourth_root_scaling_laws():
    """L2SYS-REQ-03.05 / [a: D12]: the fourth-root structure of the
    equation implies 16x RCS -> 2x range and +12 dB sensitivity -> 1/2
    range. Both derived from the formula, not from the code."""
    base = calc_radar_equation(rcs_m2=DEFAULT_OWNSHIP_RCS_M2)
    assert calc_radar_equation(rcs_m2=16.0 * DEFAULT_OWNSHIP_RCS_M2) == \
        pytest.approx(2.0 * base, rel=1e-9)
    # a receiver 16x LESS sensitive (Pmin x 16, i.e. +12.04 dB) halves the
    # range; 16 is the exact fourth-power factor the law implies.
    worse = DEFAULT_PMIN_W * 16.0
    assert calc_radar_equation(pmin_w=worse) == pytest.approx(base / 2.0,
                                                              rel=1e-9)
    # and a 16x MORE sensitive receiver doubles it
    assert calc_radar_equation(pmin_w=DEFAULT_PMIN_W / 16.0) == \
        pytest.approx(2.0 * base, rel=1e-9)


def test_03_05_detection_status_follows_slant_range(sem):
    """L2SYS-REQ-03.05: "compute each beacon's detection status ...
    against the received ownship state"; [a: D12] "detection = slant <=
    R_max"."""
    sem.ownship = OwnshipState(lat_deg=45.0, lon_deg=9.0, alt_m=0.0)
    r_max = reference_rmax(DEFAULT_PT_W, DEFAULT_GAIN_DB, DEFAULT_FREQ_HZ,
                           DEFAULT_OWNSHIP_RCS_M2, DEFAULT_PMIN_W)

    inside = sem.add_beacon(classification="FOE", range_m=r_max * 0.5,
                            bearing_deg=0.0)
    outside = sem.add_beacon(classification="FOE", range_m=r_max * 1.5,
                             bearing_deg=180.0)
    sem.evaluate_all()
    assert inside.detected is True
    assert outside.detected is False
    assert inside.slant_range_m == pytest.approx(r_max * 0.5, rel=1e-3)


def test_03_05_slant_range_includes_altitude(sem):
    """L2SYS-REQ-03.05 via TA action calcBeaconOwnshipRange: the range
    the radar equation is evaluated against is a SLANT range, so the
    ownship altitude must enter it."""
    own_low = OwnshipState(lat_deg=45.0, lon_deg=9.0, alt_m=0.0)
    own_high = OwnshipState(lat_deg=45.0, lon_deg=9.0, alt_m=10_000.0)
    ground = slant_range_m(own_low, 45.0, 9.1, 0.0)
    slant = slant_range_m(own_high, 45.0, 9.1, 0.0)
    assert slant == pytest.approx(math.hypot(ground, 10_000.0), rel=1e-9)


def test_03_05_recomputes_on_each_received_ownship_state(sem, udp_sender):
    """L2SYS-REQ-03.05: the evaluation is driven by the received ownship
    state, so moving the ownship changes the detection outcome."""
    import time
    sem.connect_to_sim()
    r_max = reference_rmax(DEFAULT_PT_W, DEFAULT_GAIN_DB, DEFAULT_FREQ_HZ,
                           DEFAULT_OWNSHIP_RCS_M2, DEFAULT_PMIN_W)
    sem.ownship = OwnshipState(lat_deg=45.0, lon_deg=9.0)
    rec = sem.add_beacon(classification="FOE", range_m=r_max * 0.5,
                         bearing_deg=0.0)
    assert rec.detected is True

    far = OwnshipState(lat_deg=45.0 - 3.0, lon_deg=9.0)   # ~333 km south
    udp_sender.sendto(far.to_json(), (ports.LOCALHOST, sem.rx_port))
    for _ in range(100):
        if sem.poll_ownship() is not None:
            break
        time.sleep(0.01)
    assert rec.detected is False


# ==========================================================================
# D2 - transmission protocol
# ==========================================================================

def test_d2_event_driven_transmits_only_the_changed_beacon(sem,
                                                           udp_receiver):
    """[a: D2] "Event-driven: transmit only the changed beacon"."""
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    ids = [sem.add_beacon(classification="FRIEND", range_m=1000.0).beacon_id
           for _ in range(3)]
    assert rx.wait_for(3)
    rx.clear()
    sem.update_beacon(ids[1], classification="FOE")
    assert rx.wait_for(1)
    import time
    time.sleep(0.15)
    assert len(rx.datagrams) == 1, "an edit transmitted more than one beacon"
    assert RadarBeacon.from_json(rx.datagrams[0]).beacon_id == ids[1]


def test_d2_background_refresh_sends_the_whole_set(sem, udp_receiver):
    """[a: D2] "background refresh every 2.0 s" of the full set."""
    rx = udp_receiver(ports.UDP_SEM_TO_ORCH)
    ids = {sem.add_beacon(classification="UNKNOWN", range_m=1000.0).beacon_id
           for _ in range(4)}
    assert rx.wait_for(4)
    rx.clear()
    assert sem.refresh_all() == 4
    assert rx.wait_for(4)
    assert {RadarBeacon.from_json(d).beacon_id for d in rx.datagrams} == ids


def test_d2_gui_refresh_timer_period_is_2s(sem_window):
    """[a: D2] the background refresh period is 2.0 s."""
    assert sem_window.refresh_timer.interval() == int(BEACON_REFRESH_S * 1000)
    assert BEACON_REFRESH_S == 2.0
