"""C-B11 airfield seed and override. [a: Behaviour B11] [a: L2SYS-REQ-01.04]"""
import json
from flightsim.pcs.airfields import load_airfields, SEED


def test_seed_and_override(tmp_path):
    t = load_airfields(None)
    assert set(t) == {"ETSI", "EDDM", "EDDN"}
    assert t["ETSI"].name == "Manching" and t["EDDN"].name == "Nuernberg"
    p = tmp_path / "airports_local.json"
    p.write_text(json.dumps({"EDDM": {"runway_hdg_deg": 261.0}}))
    t = load_airfields(str(p))
    assert t["EDDM"].runway_hdg_deg == 261.0 and t["EDDM"].lat_deg == SEED["EDDM"].lat_deg
    p.write_text("{corrupt")
    logs = []
    t = load_airfields(str(p), log=logs.append)
    assert t["EDDM"].runway_hdg_deg == SEED["EDDM"].runway_hdg_deg and logs
