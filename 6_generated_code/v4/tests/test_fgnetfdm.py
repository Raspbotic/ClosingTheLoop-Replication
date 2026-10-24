"""C-B13 FGNetFDM layout. [a: Behaviour B13] [a: fsim_TA_IF_Requirements.sysml#req_pcsNativeFdmOutPort]"""
import math, struct
from flightsim.pcs import fgnetfdm


def test_layout():
    t = dict(lat_deg=48.0, lon_deg=11.0, alt_m=450.0, roll_deg=10.0, pitch_deg=5.0, heading_deg=90.0)
    b = fgnetfdm.pack(t)
    assert len(b) == 408
    assert struct.unpack_from(">I", b, 0)[0] == 24          # version, big-endian
    lon, lat, alt = struct.unpack_from(">ddd", b, 8)
    assert abs(lon - math.radians(11.0)) < 1e-12 and abs(lat - math.radians(48.0)) < 1e-12 and alt == 450.0
    phi, theta, psi = struct.unpack_from(">fff", b, 36)
    assert abs(psi - math.radians(90.0)) < 1e-6 and abs(phi - math.radians(10.0)) < 1e-6
