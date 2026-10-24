"""C-B7 terrain endpoint handling. [a: Behaviour B7] [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml#terrainElevationManager]"""
from flightsim.pcs.io_manager import parse_ground_elev, TerrainPoller


def test_ground_elev_parse():
    assert parse_ground_elev(b'{"path":"/position/ground-elev-m","value":"452.5"}') == 452.5
    assert parse_ground_elev(b'{"value": 452.5}') == 452.5


def test_absent_endpoint_tolerated():
    got = []
    def fail(url):
        raise ConnectionRefusedError("no fgfs")
    p = TerrainPoller(got.append, get=fail)
    assert p.poll_once() is None and got == []
    p2 = TerrainPoller(got.append, get=lambda url: b'{"value":"301.0"}')
    assert p2.poll_once() == 301.0 and got == [301.0]
    assert "8080/json/position/ground-elev-m" in p2.url
