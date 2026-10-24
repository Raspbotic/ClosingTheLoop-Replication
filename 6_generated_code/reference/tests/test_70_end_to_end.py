"""Full system: HOTAS(demo) + PCS + Orchestrator + SEM over real ports.
Traces: fsim_TA_System.sysml external connections; closes every
requirement path (manual flight, threat assessment, scenario mgmt)."""
import threading, time, pytest
from flightsim.common import ports as P
from flightsim.common.messages import RadarBeacon


@pytest.mark.slow
def test_full_system_loop(qapp):
    from conftest import process_events
    from flightsim.master_node import BusOrchestrator
    from flightsim.pcs.main_window import MainWindow
    from flightsim.run_sem import SemWindow
    from flightsim.hotas_standalone import DemoSource, run as hotas_run

    orch = BusOrchestrator()
    threading.Thread(target=orch.run, kwargs={"stats_period_s": 0},
                     daemon=True).start()
    pcs = MainWindow(); pcs.show()
    sem = SemWindow(); sem.show()
    threading.Thread(target=hotas_run, args=(DemoSource(), 60.0,
        P.LOCALHOST, P.UDP_HOTAS_CONTROL), daemon=True).start()

    try:
        process_events(qapp, 6.0)     # JSBSim init + links settle
        assert sem.ownship is not None, \
            "SEM must see ownship (PCS->orch->SEM path broken)"
        assert abs(pcs.io.last_controls.throttle - 0.75) < 0.02, \
            "HOTAS demo input must reach PCS (5005 path broken)"

        b = RadarBeacon(beacon_id="E2E", classification="FOE",
                        lat_deg=sem.ownship.lat_deg + 0.05,
                        lon_deg=sem.ownship.lon_deg,
                        alt_m=150, max_range_m=30000)
        sem.beacons[b.beacon_id] = b
        sem._tx_beacon(b)
        process_events(qapp, 2.0)
        assert "E2E" in pcs.io.beacons, \
            "beacon must reach PCS (SEM->orch->PCS path broken)"
        assert "E2E" in pcs.map_window.map_widget.beacons, \
            "beacon must reach the tactical map (internal binding broken)"
        got, _ = pcs.io.beacons["E2E"]
        assert got.detected, "5.5km inside 30km envelope must be detected"

        bb = sem.beacons.pop("E2E"); bb.active = False; sem._tx_beacon(bb)
        process_events(qapp, 1.5)
        assert "E2E" not in pcs.io.beacons, "tombstone must clear PCS"
        assert "E2E" not in pcs.map_window.map_widget.beacons
    finally:
        sem.beacons.clear()
        pcs.close(); sem.close(); orch.stop()
