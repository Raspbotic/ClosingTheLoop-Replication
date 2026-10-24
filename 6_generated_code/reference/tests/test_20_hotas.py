"""HOTAS process: demo tx rate, smart-yaw pick, debug edge detection.
Traces: HotasControl (fsim_TA_Peripherals.sysml), L2SYS-REQ-01.01."""
import sys, types, time, threading
from flightsim.common import ports
from flightsim.common.messages import decode, HotasControls


def test_demo_transmits_at_60hz(udp_rx):
    rx = udp_rx(ports.UDP_HOTAS_CONTROL, timeout=2.0)
    from flightsim.hotas_standalone import DemoSource, run
    stop = threading.Event()   # must stop, or the thread keeps
    t = threading.Thread(       # transmitting into later tests [b]
        target=run, args=(DemoSource(), 60.0, ports.LOCALHOST,
                          ports.UDP_HOTAS_CONTROL),
        kwargs={"stop_event": stop}, daemon=True)
    t.start()
    try:
        msgs = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            msgs.append(decode(rx.recvfrom(4096)[0]))
        assert 50 <= len(msgs) <= 70, f"rate {len(msgs)} Hz, expected ~60"
        seqs = [m.seq for m in msgs]
        assert seqs == sorted(seqs), "sequence numbers must be monotonic"
        assert all(0.0 <= m.throttle <= 1.0 for m in msgs)
    finally:
        stop.set()
        t.join(timeout=2)


def test_smart_yaw_picks_dominant_axis(monkeypatch):
    class FakeJoystick:
        axes = [0.3, -0.2, -0.5, 0.0, 0.7]
        buttons = [False] * 8
        def init(self): pass
        def get_name(self): return "Fake"
        def get_numaxes(self): return 5
        def get_numbuttons(self): return 8
        def get_axis(self, i): return self.axes[i]
        def get_button(self, i): return self.buttons[i]
    js = FakeJoystick()
    fake = types.ModuleType("pygame")
    fake.init = lambda: None
    fake.joystick = types.SimpleNamespace(init=lambda: None,
        get_count=lambda: 1, Joystick=lambda i: js)
    fake.event = types.SimpleNamespace(pump=lambda: None,
                                       get=lambda: [])
    monkeypatch.setitem(sys.modules, "pygame", fake)
    from flightsim.hotas_standalone import JoystickSource
    src = JoystickSource(0, 0, 1, 2, [3, 4], 0.05)
    m = src.read()
    assert abs(m.yaw - 0.7) < 1e-6, "must pick axis4 (dominant)"
    js.axes[3], js.axes[4] = -0.6, 0.1
    assert abs(src.read().yaw - (-0.6)) < 1e-6, "must switch to axis3"
    assert abs(m.throttle - 0.75) < 1e-6, "throttle -0.5 -> 0.75 normalized"


def test_debug_printer_edges(capsys):
    from flightsim.hotas_standalone import DebugPrinter
    dp = DebugPrinter(refresh_hz=1000)
    dp.update(HotasControls(seq=1, switches={"btn0": False}))
    dp.update(HotasControls(seq=2, switches={"btn0": True}))
    dp.update(HotasControls(seq=3, switches={"btn0": True}))
    dp.update(HotasControls(seq=4, switches={"btn0": False}))
    out = capsys.readouterr().out
    assert out.count("button DOWN : btn0") == 1
    assert out.count("button UP   : btn0") == 1