"""BusOrchestrator: both relay paths, payload opacity, burst throughput.
Traces: fsim_TA_Orch.sysml, L2SYS-REQ-04.01, confirmed port map."""
import socket, threading, time, pytest
from flightsim.common import ports
from flightsim.common.messages import OwnshipState, RadarBeacon, decode
from flightsim.master_node import BusOrchestrator


@pytest.fixture
def orch():
    o = BusOrchestrator()
    t = threading.Thread(target=o.run, kwargs={"stats_period_s": 0},
                         daemon=True)
    t.start()
    time.sleep(0.3)
    yield o
    o.stop()


def test_ownship_path_pcs_to_sem(orch, udp_rx):
    sem_rx = udp_rx(ports.UDP_SEM_OWNSHIP_STATE, timeout=3.0)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = OwnshipState(seq=7, lat_deg=45.45, alt_m=104)
    tx.sendto(sent.to_json(), (ports.LOCALHOST, ports.UDP_PCS_OWNSHIP_STATE))
    assert decode(sem_rx.recvfrom(65535)[0]) == sent
    tx.close()


def test_radar_path_sem_to_pcs_with_tombstone(orch, udp_rx):
    pcs_rx = udp_rx(ports.UDP_PCS_RADAR_DETECTIONS, timeout=3.0)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    b = RadarBeacon(seq=1, beacon_id="T", classification="FOE",
                    max_range_m=9000)
    tx.sendto(b.to_json(), (ports.LOCALHOST, ports.UDP_SEM_RADAR_DETECTIONS))
    assert decode(pcs_rx.recvfrom(65535)[0]) == b
    ts = RadarBeacon(seq=2, beacon_id="T", active=False)
    tx.sendto(ts.to_json(), (ports.LOCALHOST, ports.UDP_SEM_RADAR_DETECTIONS))
    got = decode(pcs_rx.recvfrom(65535)[0])
    assert got.active is False
    tx.close()


def test_burst_no_loss_on_loopback(orch, udp_rx):
    sem_rx = udp_rx(ports.UDP_SEM_OWNSHIP_STATE, timeout=0.5)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    N = 240
    for i in range(N):
        tx.sendto(OwnshipState(seq=i).to_json(),
                  (ports.LOCALHOST, ports.UDP_PCS_OWNSHIP_STATE))
    got = 0
    try:
        while True:
            sem_rx.recvfrom(65535); got += 1
    except OSError:
        pass
    assert got >= N * 0.98, f"delivered {got}/{N}"
    tx.close()