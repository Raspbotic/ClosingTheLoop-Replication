"""L2SYS-REQ-04.01 BusOrchestrator rows. [a: Behaviour B3] [a: fsim_TA_Orch.sysml]"""
import time
import pytest

from flightsim.common import ports, messages as m
from flightsim.orch.master_node import BusOrchestrator
from tests.conftest import send_udp


@pytest.fixture
def orch():
    o = BusOrchestrator().start()
    yield o
    o.stop()


def _own():
    return m.encode(m.OwnshipState(seq=1, sim_time_s=0.0, lat_deg=48.0, lon_deg=11.0, alt_m=500.0, agl_m=0.0,
                                   v_speed_mps=0.0, speed_mps=0.0, heading_deg=0.0, pitch_deg=0.0, roll_deg=0.0))


def test_pcs_to_sem_path(orch, udp_receiver):
    """L2SYS-REQ-04.01: ownship from PCS (5006) reaches SEM (5007) unchanged."""
    rx = udp_receiver(ports.SEM_OWNSHIP_IN_PORT)
    send_udp(ports.PCS_OWNSHIP_OUT_PORT, _own())
    assert rx.collect(0.3) == [_own()]


def test_sem_to_pcs_path(orch, udp_receiver):
    """L2SYS-REQ-04.01: beacon from SEM (6007) reaches PCS (6006) unchanged."""
    rx = udp_receiver(ports.PCS_RADAR_IN_PORT)
    b = m.encode(m.RadarBeacon(beacon_id=1, classification="FOE", lat_deg=48, lon_deg=11, alt_m=0, max_range_m=1000))
    send_udp(ports.SEM_RADAR_OUT_PORT, b)
    assert rx.collect(0.3) == [b]


def test_tombstone_transit(orch, udp_receiver):
    """L2SYS-REQ-04.01 / B2: tombstone passes the relay."""
    rx = udp_receiver(ports.PCS_RADAR_IN_PORT)
    b = m.RadarBeacon(beacon_id=9, classification="UNKNOWN", lat_deg=48, lon_deg=11, alt_m=0, max_range_m=1000, active=False)
    send_udp(ports.SEM_RADAR_OUT_PORT, m.encode(b))
    got = rx.collect(0.3)
    assert len(got) == 1 and m.decode(got[0]).active is False


def test_opaque_payload(orch, udp_receiver):
    """B3 payload-opaque: non-JSON bytes relayed unchanged."""
    rx = udp_receiver(ports.SEM_OWNSHIP_IN_PORT)
    send_udp(ports.PCS_OWNSHIP_OUT_PORT, b"\x00\xffopaque")
    assert rx.collect(0.3) == [b"\x00\xffopaque"]


def test_nonblocking_burst(orch, udp_receiver):
    """L2SYS-REQ-04.01: a 2000-datagram burst is relayed without blocking the caller;
    relay also continues with no receiver on the far end."""
    import socket
    rx = udp_receiver(ports.SEM_OWNSHIP_IN_PORT)
    rx.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0 = time.monotonic()
    for _ in range(2000):
        s.sendto(_own(), ("127.0.0.1", ports.PCS_OWNSHIP_OUT_PORT))
    dt = time.monotonic() - t0
    s.close()
    got = rx.collect(1.0)
    assert dt < 1.0
    assert len(got) >= 1800
    assert orch.pcsNode.sock.getblocking() is False and orch.seNode.sock.getblocking() is False
    # far end absent: SEM->PCS path with nothing bound on 6006
    send_udp(ports.SEM_RADAR_OUT_PORT, b"x")
    time.sleep(0.1)
    assert orch._thread.is_alive()
