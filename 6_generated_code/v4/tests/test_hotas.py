"""HOTAS transmitter rows. [a: L2SYS-REQ-01.01b] [a: Behaviour B5]"""
import threading
import time

from flightsim.common import messages as m
from flightsim.common import ports
from flightsim.hotas.hotas_standalone import HotasTransmitter, DemoSource, AxisMapper


def _run(tx, seconds):
    stop = threading.Event()
    t = threading.Thread(target=tx.run, args=(stop,), name="hotas-tx")
    t.start()
    time.sleep(seconds)
    stop.set()
    t.join(2)
    assert not t.is_alive()


def test_demo_transmits_on_5005(udp_receiver):
    """L2SYS-REQ-01.01b: axes, throttle and buttons polled and transmitted via UDP 5005."""
    rx = udp_receiver(ports.HOTAS_CONTROL_PORT)
    tx = HotasTransmitter(DemoSource())
    stop = threading.Event()
    t = threading.Thread(target=tx.run, args=(stop,), name="hotas-tx")
    t.start()
    data = rx.collect(0.5)
    stop.set(); t.join(2)
    msgs = [m.decode(d) for d in data]
    assert msgs and all(isinstance(x, m.HotasControls) for x in msgs)
    assert len({x.roll for x in msgs}) > 1  # continuously polled
    assert [x.seq for x in msgs] == sorted(x.seq for x in msgs)


def test_rate_60hz(udp_receiver):
    """L2SYS-REQ-01.01b: continuous transmission at the HOTAS rate (60 Hz) measured over 1 s."""
    rx = udp_receiver(ports.HOTAS_CONTROL_PORT)
    tx = HotasTransmitter(DemoSource())
    stop = threading.Event()
    t = threading.Thread(target=tx.run, args=(stop,), name="hotas-tx")
    t.start()
    rx.collect(0.2)
    n = len(rx.collect(1.0))
    stop.set(); t.join(2)
    assert 54 <= n <= 66


def test_stop_event_halts_traffic(udp_receiver):
    """L2SYS-REQ-01.01b / Conv: no datagram on 5005 after teardown."""
    rx = udp_receiver(ports.HOTAS_CONTROL_PORT)
    _run(HotasTransmitter(DemoSource()), 0.2)
    rx.collect(0.1)
    assert rx.collect(0.3) == []


def test_smart_yaw():
    """C-B5: the larger deflection of candidate axes 3/4 drives yaw."""
    mp = AxisMapper()
    f, _ = mp.map([0, 0, 1, 0.3, -0.6], [])
    assert f["yaw"] == -0.6
    f, _ = mp.map([0, 0, 1, 0.8, -0.6], [])
    assert f["yaw"] == 0.8
    f, _ = mp.map([0.02, -0.03, 1, 0.01, 0.0], [])
    assert f["roll"] == 0 and f["pitch"] == 0 and f["yaw"] == 0
    f, _ = mp.map([0, 0, -1.0], [])
    assert f["throttle"] == 1.0
    f, _ = mp.map([0, 0, 1.0], [])
    assert f["throttle"] == 0.0


def test_button_map():
    """C-B5: btn2 gear toggle, btn4 air-brake toggle (edges), btn6 momentary wheel brake."""
    mp = AxisMapper()
    b = [False] * 8
    f, _ = mp.map([0, 0, 1], b)
    assert (f["gear_toggles"], f["airbrake_toggles"], f["wheel_brake"]) == (0, 0, False)
    b2 = list(b); b2[2] = True; b2[4] = True; b2[6] = True
    f, _ = mp.map([0, 0, 1], b2)
    f, _ = mp.map([0, 0, 1], b2)  # held: no new edge
    assert (f["gear_toggles"], f["airbrake_toggles"], f["wheel_brake"]) == (1, 1, True)
    f, _ = mp.map([0, 0, 1], b)
    assert f["wheel_brake"] is False and f["gear_toggles"] == 1


class _FlakySource:
    """Device present, removed, reconnected."""
    def __init__(self):
        self.n = 0
    def poll(self):
        self.n += 1
        return None if 3 <= self.n <= 5 else ([0.0, 0.0, 1.0], [False] * 8)


def test_suspend_while_device_absent(udp_receiver):
    """C-B5 hot-plug: no transmission while the source reports no device."""
    rx = udp_receiver(ports.HOTAS_CONTROL_PORT)
    tx = HotasTransmitter(_FlakySource())
    sent = [tx.step() for _ in range(7)]
    tx.sock.close()
    assert sent == [True, True, False, False, False, True, True]
    assert len(rx.collect(0.1)) == 4


def test_invert_pitch_option():
    """C-B5 / M12 field fallback: --invert-pitch flips axis 1 only."""
    f, _ = AxisMapper(invert_pitch=True).map([0.2, 0.5, 1.0], [])
    assert f["pitch"] == -0.5 and f["roll"] == 0.2


def test_brake_button_configurable(udp_receiver):
    """L2SYS-REQ-01.01b / B5 (field defect D-3): the wheel-brake button index is configurable;
    the transmitted datagram carries wheel_brake while that button is held; --debug names the index."""
    mp = AxisMapper(btn_wheelbrake=1)
    b = [False] * 8
    b[1] = True
    f, ev = mp.map([0, 0, 1], b)
    assert f["wheel_brake"] is True and "[BTN 1] pressed" in ev
    b[1] = False; b[6] = True
    f, _ = mp.map([0, 0, 1], b)
    assert f["wheel_brake"] is False          # default index no longer used
    assert AxisMapper(btn_wheelbrake=9).map([0, 0, 1], [True] * 4)[0]["wheel_brake"] is False  # out of range: safe

    class Held:
        def poll(self):
            return [0.0, 0.0, 1.0], [False, False, False, True]
    rx = udp_receiver(ports.HOTAS_CONTROL_PORT)
    tx = HotasTransmitter(Held(), buttons=dict(btn_wheelbrake=3))
    tx.step(); tx.sock.close()
    got = [m.decode(d) for d in rx.collect(0.1)]
    assert got and got[0].wheel_brake is True


def test_cli_button_options():
    """B5: --btn-gear/--btn-airbrake/--btn-brake reach the mapper."""
    import flightsim.hotas.hotas_standalone as hs
    seen = {}
    class FakeTx:
        def __init__(self, src, **kw):
            seen.update(kw["buttons"])
        def run(self):
            pass
    orig_tx, orig_src = hs.HotasTransmitter, hs.DemoSource
    hs.HotasTransmitter = FakeTx
    try:
        hs.main(["--demo", "--btn-brake", "0", "--btn-gear", "5", "--btn-airbrake", "7"])
    finally:
        hs.HotasTransmitter = orig_tx
    assert seen == dict(btn_gear=5, btn_airbrake=7, btn_wheelbrake=0)
