"""
tests/test_hotas.py
===================
Verification-plan rows allocated to the HOTAS transmitter module:
L2SYS-REQ-01.01b and the D7 yaw-selection conformance check.

[a: D15] every transmit thread carries a stop_event and is joined in
teardown; no port 5005 traffic survives a test.
"""

from __future__ import annotations

import threading
import time

import pytest

from flightsim import hotas_standalone as hotas
from flightsim.common import ports
from flightsim.common.messages import HotasControls


class StubSource:
    """Hardware-free control source with known, fixed values. [c] test aid."""

    def __init__(self, msg=None):
        self.msg = msg or HotasControls(
            roll=-0.40, pitch=0.30, yaw=0.20, throttle=0.65,
            switches={"btn2": True, "btn4": False, "btn6": True})
        self.reads = 0

    def read(self):
        self.reads += 1
        # a fresh object each frame, as a real source would produce
        return HotasControls(roll=self.msg.roll, pitch=self.msg.pitch,
                             yaw=self.msg.yaw, throttle=self.msg.throttle,
                             switches=dict(self.msg.switches))


class SilentSource:
    """Emulates an unplugged device: read() returns None. [a] R-4 hot-plug."""

    def read(self):
        return None


def _run_transmitter(source, rate_hz=60.0, port=None):
    """Start the transmit loop on a stoppable thread. [a: D15]"""
    stop = threading.Event()
    t = threading.Thread(
        target=hotas.run, args=(source,),
        kwargs=dict(rate_hz=rate_hz, host=ports.LOCALHOST,
                    port=port or ports.UDP_HOTAS_CONTROL, stop_event=stop),
        daemon=True, name="hotas-tx-test")
    t.start()
    return stop, t


# ==========================================================================
# L2SYS-REQ-01.01b
# ==========================================================================

def test_01_01b_transmits_axes_and_buttons_over_udp(udp_receiver):
    """L2SYS-REQ-01.01b: the HOTAS shall continuously poll the physical
    joystick axes, throttle lever and buttons, and transmit the associated
    signals via UDP to the Virtual Ownship.

    Stimulus: run the transmit loop over a stub source with known values.
    Observation: the D4 HOTAS->PCS port (5005).
    Pass: the decoded datagram reproduces every axis and the button map.
    """
    rx = udp_receiver(ports.UDP_HOTAS_CONTROL)
    src = StubSource()
    stop, thread = _run_transmitter(src)
    try:
        assert rx.wait_for(3, timeout=2.0), "no HOTAS datagrams on port 5005"
    finally:
        stop.set()
        thread.join(timeout=2.0)

    msg = HotasControls.from_json(rx.datagrams[0])
    assert msg.roll == pytest.approx(-0.40)
    assert msg.pitch == pytest.approx(0.30)
    assert msg.yaw == pytest.approx(0.20)
    assert msg.throttle == pytest.approx(0.65)
    assert msg.button(2) is True and msg.button(6) is True
    assert msg.button(4) is False


def test_01_01b_transmit_rate_is_continuous(udp_receiver):
    """L2SYS-REQ-01.01b ('continuously ... transmit'): measure the rate.

    Pass criterion (plan): at least 0.7 x the configured rate is observed
    over a 1 s window, and the sequence numbers increase monotonically.
    """
    rate = 60.0
    rx = udp_receiver(ports.UDP_HOTAS_CONTROL)
    stop, thread = _run_transmitter(StubSource(), rate_hz=rate)
    try:
        time.sleep(1.0)
    finally:
        stop.set()
        thread.join(timeout=2.0)

    n = len(rx.datagrams)
    assert n >= 0.7 * rate, f"only {n} datagrams in 1 s at {rate} Hz"
    seqs = [HotasControls.from_json(d).seq for d in rx.datagrams]
    assert seqs == sorted(seqs) and seqs[0] == 0
    assert len(set(seqs)) == len(seqs)


def test_01_01b_no_traffic_when_device_absent(udp_receiver):
    """L2SYS-REQ-01.01b + R-4 hot-plug tolerance: with no device present
    the transmitter stays silent, so the PCS virtual HOTAS can take over
    [a: D10 physical-precedence window].
    """
    rx = udp_receiver(ports.UDP_HOTAS_CONTROL)
    stop, thread = _run_transmitter(SilentSource())
    try:
        time.sleep(0.4)
    finally:
        stop.set()
        thread.join(timeout=2.0)
    assert rx.datagrams == []


def test_01_01b_transmitter_stops_cleanly(udp_receiver):
    """L2SYS-REQ-01.01b teardown, [a: D15]: no port 5005 traffic after
    the transmit thread has been stopped."""
    rx = udp_receiver(ports.UDP_HOTAS_CONTROL)
    stop, thread = _run_transmitter(StubSource())
    assert rx.wait_for(2, timeout=2.0)
    stop.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    time.sleep(0.15)
    rx.clear()
    time.sleep(0.25)
    assert rx.datagrams == [], "transmitter still emitting after stop"


# ==========================================================================
# D7 - smart yaw
# ==========================================================================

@pytest.mark.parametrize("candidates,expected", [
    ((0.10, 0.80), 0.80),        # axis 4 deflected more
    ((-0.90, 0.20), -0.90),      # axis 3 deflected more, sign preserved
    ((0.0, 0.0), 0.0),           # both centred
    ((0.50, -0.51), -0.51),      # near tie: magnitude decides
    ((0.30,), 0.30),             # single candidate configuration
])
def test_d7_smart_yaw_picks_the_larger_deflection(candidates, expected):
    """[a: D7] 'HOTAS yaw axis: pick the larger deflection of candidate
    axes 3/4 (smart yaw, per legacy hardware).'
    [a: legacy hotas_standalone.py 'Smart Rudder Detection']"""
    assert hotas.smart_yaw(candidates) == pytest.approx(expected)


def test_d7_default_yaw_candidates_are_axes_3_and_4():
    """[a: D7] the candidate set is axes 3/4 by default;
    [a: legacy file] roll=0, pitch=1, throttle=2."""
    args = hotas.build_parser().parse_args([])
    assert args.yaw_axes == [3, 4]
    assert (args.roll_axis, args.pitch_axis, args.throttle_axis) == (0, 1, 2)


def test_throttle_axis_normalized_to_wire_range():
    """L2SYS-REQ-01.01b (the throttle lever signal) with [a: D1]
    "throttle in [0,1]": the [c] developer choice documented at the module
    head maps the hardware's +1..-1 range onto the wire range."""
    assert hotas.normalize_throttle(1.0) == pytest.approx(0.0)
    assert hotas.normalize_throttle(0.0) == pytest.approx(0.5)
    assert hotas.normalize_throttle(-1.0) == pytest.approx(1.0)


def test_demo_source_is_hardware_free_and_in_range():
    """L2SYS-REQ-01.01b support path: [c] --demo synthesizes inputs so
    the R-9 end-to-end test needs no hardware; the values must still
    satisfy the [a: D1] wire ranges."""
    src = hotas.DemoSource()
    msg = src.read()
    assert -1.0 <= msg.roll <= 1.0 and -1.0 <= msg.pitch <= 1.0
    assert 0.0 <= msg.throttle <= 1.0
