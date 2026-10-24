"""Interface layer: port constants vs TA model, message wire protocol.
Traces: fsim_TA_Ports.sysml, TA port numbers, beacon protocol decisions."""
import pytest
from flightsim.common import ports
from flightsim.common.messages import (HotasControls, OwnshipState,
    RadarBeacon, decode, MessageError, BEACON_REFRESH_PERIOD_S,
    BEACON_STALE_TIMEOUT_S)


class TestPortAssignments:
    """Each constant vs the TA-confirmed port map (2026-07-11)."""
    def test_hotas_control_port(self):
        assert ports.UDP_HOTAS_CONTROL == 5005
    def test_pcs_ownship_out(self):
        assert ports.UDP_PCS_OWNSHIP_STATE == 5006
    def test_sem_ownship_in(self):
        assert ports.UDP_SEM_OWNSHIP_STATE == 5007
    def test_pcs_radar_in(self):
        assert ports.UDP_PCS_RADAR_DETECTIONS == 6006
    def test_sem_radar_out_link(self):
        assert ports.UDP_SEM_RADAR_DETECTIONS == 6007
    def test_native_fdm(self):
        assert ports.UDP_NATIVE_FDM == 5550
    def test_ground_elev_http(self):
        assert ports.HTTP_GROUND_ELEV == 8080
    def test_osm_host(self):
        assert "tile.openstreetmap.org" in ports.OSM_TILE_URL_TEMPLATE


class TestMessageRoundTrips:
    def test_hotas_roundtrip(self):
        m = HotasControls(seq=9, roll=0.5, pitch=-0.2, yaw=0.1,
                          throttle=0.8, switches={"btn2": True})
        assert decode(m.to_json()) == m
    def test_ownship_roundtrip(self):
        m = OwnshipState(seq=3, lat_deg=45.45, lon_deg=9.28, alt_m=104.0,
                         heading_deg=356.0)
        assert decode(m.to_json()) == m
    def test_beacon_roundtrip(self):
        m = RadarBeacon(beacon_id="SAM-1", classification="FOE",
                        max_range_m=25000, detected=True, slant_range_m=5000)
        assert decode(m.to_json()) == m
    def test_malformed_rejected(self):
        with pytest.raises(MessageError):
            decode(b"not json at all")
        with pytest.raises(MessageError):
            decode(b'{"msg_type": "unknown_thing"}')


class TestBeaconProtocol:
    def test_tombstone_semantics(self):
        table = {}
        create = RadarBeacon(beacon_id="X", classification="FOE")
        tomb = RadarBeacon(beacon_id="X", active=False)
        for msg in (create, tomb):
            if msg.active:
                table[msg.beacon_id] = msg
            else:
                table.pop(msg.beacon_id, None)
        assert "X" not in table
    def test_stale_exceeds_refresh(self):
        """A single lost refresh must never evict a live beacon."""
        assert BEACON_STALE_TIMEOUT_S >= 2 * BEACON_REFRESH_PERIOD_S


def test_airport_local_override(tmp_path, monkeypatch):
    """[bug fix 2026-07-12] airports_local.json overrides seed values
    per-ICAO with partial fields; bad JSON never kills the process."""
    import importlib, json, os
    monkeypatch.chdir(tmp_path)
    json.dump({"LIML": {"lat_deg": 45.11111, "rwy_heading_deg": 350.0}},
              open("airports_local.json", "w"))
    import flightsim.pcs.airports as ap
    importlib.reload(ap)
    a = ap.AIRPORTS["LIML"]
    assert a.lat_deg == 45.11111
    assert a.rwy_heading_deg == 350.0
    assert a.elev_m == 103.0, "non-overridden fields must persist"
    # bad JSON must be survivable
    open("airports_local.json", "w").write("{ not json")
    importlib.reload(ap)
    assert "LIML" in ap.AIRPORTS
    # restore pristine module state for later tests
    os.remove("airports_local.json")
    importlib.reload(ap)