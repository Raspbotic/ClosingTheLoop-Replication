"""
tests/test_interfaces.py
========================
D1-D4 interface-decision conformance rows of the frozen verification plan
(docs/VERIFICATION_PLAN.md section 2).

These verify specification items of the Decision Annex, so they carry
[a: Dn] citations rather than requirement IDs, except where a workbook
requirement or an IF requirement of fsim_TA_IF_Requirements.sysml also
constrains the same value.
"""

from __future__ import annotations

import json
import math

import pytest

from flightsim.common import ports
from flightsim.common.messages import (
    BEACON_REFRESH_S, BEACON_STALE_S, CLASSIFICATIONS, BeaconStore,
    HotasControls, MessageError, OwnshipState, RadarBeacon,
)


# ==========================================================================
# D4 - port map. One assertion per constant; each fails if the constant
# changes. [a: D4]
# ==========================================================================

def test_d4_port_hotas_to_pcs_is_5005():
    """[a: D4] HOTAS->PCS is UDP 5005.
    Also fixes HotasControl.e_hotasControlOut (fsim_TA_Peripherals.sysml)."""
    assert ports.UDP_HOTAS_CONTROL == 5005


def test_d4_port_pcs_to_orchestrator_is_5006():
    """[a: D4] PCS->orch is UDP 5006 (BusOrchestrator.pcsNode
    .e_ownshipStateIn, fsim_TA_Orch.sysml)."""
    assert ports.UDP_PCS_TO_ORCH == 5006


def test_d4_port_orchestrator_to_sem_is_5007():
    """[a: D4] orch->SEM is UDP 5007. Also req_semRadarPort.expectedPort
    = 5007 (fsim_TA_IF_Requirements.sysml)."""
    assert ports.UDP_ORCH_TO_SEM == 5007


def test_d4_port_sem_to_orchestrator_is_6007():
    """[a: D4] SEM->orch is UDP 6007 (SyntheticEnvironmentMgr
    .e_radarDetectionsOut, fsim_TA_SEM.sysml)."""
    assert ports.UDP_SEM_TO_ORCH == 6007


def test_d4_port_orchestrator_to_pcs_is_6006():
    """[a: D4] orch->PCS is UDP 6006. Also req_pcsRadarPort.expectedPort
    = 6006 (fsim_TA_IF_Requirements.sysml)."""
    assert ports.UDP_ORCH_TO_PCS == 6006


def test_d4_port_pcs_to_flightgear_fdm_is_5550():
    """[a: D4] PCS->FlightGear native-fdm is UDP 5550
    (ThreadPhysics.e_nativeJSBSimFdmOut)."""
    assert ports.UDP_FG_NATIVE_FDM == 5550


def test_d4_port_flightgear_httpd_is_8080():
    """[a: D4] FlightGear httpd is 8080
    (FlightGearOTW.localHttpServer.e_groundElevMeterOut)."""
    assert ports.HTTP_FG_HTTPD == 8080


def test_d4_ground_elevation_path():
    """[a: D4] ground elevation at /json/position/ground-elev-m."""
    assert ports.FG_GROUND_ELEV_PATH == "/json/position/ground-elev-m"


def test_d4_osm_tile_endpoint():
    """[a: D4] OSM tiles at https://tile.openstreetmap.org; the host also
    fixes OpenStreetMapAPI.e_tile2DInterface address."""
    assert ports.OSM_TILE_BASE_URL == "https://tile.openstreetmap.org"
    assert ports.OSM_TILE_HOST == "tile.openstreetmap.org"
    assert ports.OSM_TILE_URL_TEMPLATE.startswith(ports.OSM_TILE_BASE_URL)


def test_d4_all_udp_ports_are_distinct():
    """[a: D4] the five federation ports plus the FDM port are distinct;
    a copy/paste error in ports.py would collapse two links."""
    values = [ports.UDP_HOTAS_CONTROL, ports.UDP_PCS_TO_ORCH,
              ports.UDP_ORCH_TO_SEM, ports.UDP_SEM_TO_ORCH,
              ports.UDP_ORCH_TO_PCS, ports.UDP_FG_NATIVE_FDM]
    assert len(set(values)) == len(values)


def test_01_02b_physics_rate_constant_is_120hz():
    """L2SYS-REQ-01.02b: the physical state is computed at 120 Hz - the
    declared rate constant. The measured rate is verified in
    tests/test_physics.py."""
    assert ports.PHYSICS_RATE_HZ == 120.0


# ==========================================================================
# D1 - wire format
# ==========================================================================

def test_d1_hotas_controls_field_set():
    """[a: D1] HotasControls carries exactly seq, roll, pitch, yaw,
    throttle, switches."""
    got = set(HotasControls().to_dict())
    assert got == {"seq", "roll", "pitch", "yaw", "throttle", "switches"}


def test_d1_hotas_controls_round_trip():
    """[a: D1] JSON-over-UDP round trip is lossless."""
    msg = HotasControls(roll=-0.5, pitch=0.25, yaw=0.75, throttle=0.4,
                        switches={"btn2": True, "btn4": False}, seq=17)
    back = HotasControls.from_json(msg.to_json())
    assert back == msg
    assert back.button(2) is True and back.button(4) is False


def test_d1_hotas_ranges_enforced():
    """[a: D1] roll/pitch/yaw in [-1,1], throttle in [0,1]."""
    msg = HotasControls(roll=5.0, pitch=-9.0, yaw=3.0, throttle=7.0)
    assert (msg.roll, msg.pitch, msg.yaw, msg.throttle) == (1.0, -1.0, 1.0, 1.0)
    assert HotasControls(throttle=-4.0).throttle == 0.0


def test_d1_ownship_state_round_trip():
    """[a: D1] OwnshipState round-trips; L2SYS-REQ-01.02b names position,
    attitude and velocity, all of which must survive the wire."""
    msg = OwnshipState(lat_deg=45.44, lon_deg=9.28, alt_m=1234.5,
                       roll_deg=-3.5, pitch_deg=7.25, heading_deg=271.0,
                       speed_mps=180.0, vspeed_mps=-4.5, on_ground=False,
                       fuel_kg=2000.0, rpm_pct=88.0, seq=3)
    back = OwnshipState.from_json(msg.to_json())
    assert back == msg
    assert back.lat_deg == pytest.approx(45.44)
    assert back.on_ground is False


def test_d1_radar_beacon_field_set_and_round_trip():
    """[a: D1] RadarBeacon carries exactly beacon_id, classification,
    lat/lon/alt, max_range_m, detected, slant_range_m, active."""
    b = RadarBeacon(beacon_id=7, classification="FOE", lat_deg=45.0,
                    lon_deg=9.0, alt_m=0.0, max_range_m=39000.0,
                    detected=True, slant_range_m=12000.0, active=True)
    assert set(b.to_dict()) == {
        "beacon_id", "classification", "lat_deg", "lon_deg", "alt_m",
        "max_range_m", "detected", "slant_range_m", "active"}
    assert RadarBeacon.from_json(b.to_json()) == b


def test_d1_classifications_are_friend_foe_unknown():
    """[a: D1] classification FRIEND/FOE/UNKNOWN. Also
    L2SYS-REQ-03.02 ('classify (FRIEND, FOE, UNKNOWN)')."""
    assert set(CLASSIFICATIONS) == {"FRIEND", "FOE", "UNKNOWN"}
    for c in CLASSIFICATIONS:
        assert RadarBeacon(classification=c).classification == c


@pytest.mark.parametrize("raw", [
    b"",                                   # empty datagram
    b"not json at all",                    # not JSON
    b"[1,2,3]",                            # JSON but not an object
    b'{"beacon_id": "abc"}',               # wrong scalar type
    b'{"classification": "HOSTILE"}',      # not an allowed enumerant
    b'{"lat_deg": "NaN-ish"}',             # unparsable float
    b'{"surprise": 1}',                    # unknown field
    b"\xff\xfe\x00",                       # undecodable bytes
])
def test_d1_malformed_datagram_rejected(raw):
    """[a: D1] 'Malformed datagrams rejected with a dedicated error type.'"""
    with pytest.raises(MessageError):
        RadarBeacon.from_json(raw)


def test_d1_malformed_hotas_datagram_rejected():
    """[a: D1] the dedicated error type applies to every wire message."""
    with pytest.raises(MessageError):
        HotasControls.from_json(b'{"switches": 5}')
    with pytest.raises(MessageError):
        HotasControls.from_json(b'{"roll": "left"}')


def test_d1_non_finite_rejected():
    """[a: D1] a non-finite scalar cannot represent a valid signal."""
    with pytest.raises(MessageError):
        HotasControls(roll=float("nan"))
    with pytest.raises(MessageError):
        RadarBeacon.from_json(json.dumps(
            {"beacon_id": 1, "lat_deg": float("inf")}).encode())


# ==========================================================================
# D2 - beacon protocol
# ==========================================================================

def test_d2_refresh_and_stale_values():
    """[a: D2] background refresh every 2.0 s; stale > 5.0 s."""
    assert BEACON_REFRESH_S == 2.0
    assert BEACON_STALE_S == 5.0


def test_d2_stale_is_at_least_twice_refresh():
    """[a: D2] invariant 'stale >= 2 x refresh'."""
    assert BEACON_STALE_S >= 2.0 * BEACON_REFRESH_S


def test_d2_store_rejects_a_stale_window_below_the_invariant():
    """[a: D2] the invariant is enforced, not merely documented."""
    with pytest.raises(MessageError):
        BeaconStore(stale_s=3.0)


def test_d2_tombstone_semantics():
    """[a: D2] deletion via tombstone (active=False)."""
    live = RadarBeacon(beacon_id=4, classification="FOE", max_range_m=1000.0)
    assert live.is_tombstone is False
    dead = live.tombstone()
    assert dead.active is False and dead.is_tombstone is True
    assert dead.beacon_id == live.beacon_id

    store = BeaconStore()
    store.apply(live, now=100.0)
    assert 4 in store
    store.apply(dead, now=100.1)
    assert 4 not in store and len(store) == 0


def test_d2_receiver_garbage_collects_stale_beacons():
    """[a: D2] 'receiver garbage-collects beacons stale > 5.0 s'."""
    store = BeaconStore()
    store.apply(RadarBeacon(beacon_id=1, classification="FRIEND"), now=0.0)
    store.apply(RadarBeacon(beacon_id=2, classification="FOE"), now=4.0)
    store.collect(now=4.9)                    # nothing older than 5 s yet
    assert len(store) == 2
    store.collect(now=5.5)                    # beacon 1 is 5.5 s old
    assert 1 not in store and 2 in store
    store.collect(now=9.5)
    assert len(store) == 0


def test_d2_refreshed_beacon_is_not_collected():
    """[a: D2] the refresh loop keeps a live beacon alive across the
    stale window."""
    store = BeaconStore()
    b = RadarBeacon(beacon_id=3, classification="UNKNOWN")
    t = 0.0
    for _ in range(10):                      # 10 refreshes at the D2 period
        store.apply(b, now=t)
        t += BEACON_REFRESH_S
        store.collect(now=t)
        assert 3 in store


# ==========================================================================
# D1/D4 over a real socket - the constants are actually bindable and the
# messages actually survive a datagram hop.
# ==========================================================================

def test_d1_d4_round_trip_over_a_real_udp_socket(udp_receiver, udp_sender):
    """[a: D1][a: D4] a RadarBeacon survives a real UDP hop on the real
    orch->PCS port. CAVEAT [a: D15]: close running instances first."""
    rx = udp_receiver(ports.UDP_ORCH_TO_PCS)
    b = RadarBeacon(beacon_id=9, classification="FRIEND", lat_deg=45.5,
                    lon_deg=9.5, max_range_m=25000.0, slant_range_m=100.0,
                    detected=True)
    udp_sender.sendto(b.to_json(), (ports.LOCALHOST, ports.UDP_ORCH_TO_PCS))
    assert rx.wait_for(1), "no datagram received on the D4 orch->PCS port"
    assert RadarBeacon.from_json(rx.datagrams[0]) == b
