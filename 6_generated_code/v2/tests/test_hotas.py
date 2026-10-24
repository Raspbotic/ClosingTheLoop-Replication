"""HOTAS transmitter.

This module verifies L2SYS-REQ-01.01b throughout: "The HOTAS shall continuously
poll the physical joystick axes, throttle lever and buttons, and transmit the
associated signals via UDP to the Virtual Ownship." [a: D15] permits the
requirement id in the module docstring where a module verifies one requirement
throughout; rows that verify something else cite it in their own docstring.

Plan rows: P-01.01b-1 .. P-01.01b-10.

Every row runs with **no joystick attached and pygame never imported**.
[a: Sec.3 criterion 6] [a: D18]
"""

from __future__ import annotations

import socket
import sys
import threading

import pytest

from flightsim import hotas_standalone as hotas
from flightsim.common import messages, ports
from flightsim.common.messages import HotasControls

# ---------------------------------------------------------------------------
# Reference values transcribed from Sec.4.4 and D7.
# ---------------------------------------------------------------------------

# [a: Sec.4.4] roll 0, pitch 1, throttle 2, yaw candidates 3 and 4.
S44_AXIS_ROLL, S44_AXIS_PITCH, S44_AXIS_THROTTLE = 0, 1, 2
S44_AXIS_YAW_CANDIDATES = (3, 4)
S44_DEADZONE = 0.05


class StubDevice:
    """A device that reports exactly what a row tells it to.

    No pygame, no joystick, no hardware. [a: D18]
    """

    def __init__(self, axes=None, buttons=None, present=True):
        self.axes = list(axes if axes is not None else [0.0, 0.0, 1.0, 0.0, 0.0])
        self.buttons = dict(buttons or {})
        self.present = present
        self._open = False
        self.opened_times = 0
        self.open_attempts = 0
        self.removed = False

    # -- DeviceSource protocol -------------------------------------------
    @property
    def connected(self):
        return self._open

    @property
    def description(self):
        return "stub device"

    def open(self):
        self.open_attempts += 1
        if not self.present:
            return False
        self._open = True
        self.opened_times += 1
        return True

    def close(self):
        self._open = False

    def poll(self):
        if not self._open:
            return None
        if self.removed:
            self.close()
            return None
        a = self.axes
        def ax(i, default=0.0):
            return a[i] if len(a) > i else default
        return hotas.Reading(
            roll=hotas.apply_deadzone(ax(S44_AXIS_ROLL)),
            pitch=hotas.apply_deadzone(-ax(S44_AXIS_PITCH)),
            yaw=hotas.select_yaw(ax(S44_AXIS_YAW_CANDIDATES[0]),
                                 ax(S44_AXIS_YAW_CANDIDATES[1])),
            throttle=hotas.normalize_throttle(ax(S44_AXIS_THROTTLE, 1.0)),
            switches={str(k): bool(v) for k, v in self.buttons.items()},
        )


@pytest.fixture
def loopback_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yield s
    s.close()


def transmitter(source, clock, sock, **kw):
    return hotas.Transmitter(
        source, host="127.0.0.1", port=ports.HOTAS_TO_PCS, sock=sock,
        clock=clock.monotonic, sleep=clock.sleep, **kw
    )


# ===========================================================================
# P-01.01b-1 -- a datagram reaches the real port and decodes
# ===========================================================================
def test_demo_source_transmits_on_5005(udp_sink, clock, loopback_socket):
    """P-01.01b-1. The transmitter puts a decodable control message on the port
    D4 assigns to the HOTAS link, with no hardware of any kind.
    """
    sink = udp_sink(ports.HOTAS_TO_PCS)
    tx = transmitter(hotas.DemoDeviceSource(clock=clock.monotonic),
                     clock, loopback_socket)
    tx.run(max_iterations=5)
    assert sink.wait_for(1), "no datagram reached port 5005"
    msg = messages.decode(sink.datagrams[0])
    assert isinstance(msg, HotasControls)
    assert -1.0 <= msg.roll <= 1.0
    assert -1.0 <= msg.pitch <= 1.0
    assert -1.0 <= msg.yaw <= 1.0
    assert 0.0 <= msg.throttle <= 1.0
    assert isinstance(msg.switches, dict)
    tx.close()


# ===========================================================================
# P-01.01b-2 -- the rate, measured
# ===========================================================================
def test_transmit_rate_is_60hz_measured(clock, loopback_socket):
    """P-01.01b-2. The rate is *measured* -- datagrams counted against a clock
    the row controls -- not read out of a constant, and not dependent on how
    fast this machine is. [a: R-2] [a: D18]
    """
    device = StubDevice()
    tx = transmitter(device, clock, loopback_socket)

    t_start = clock.time()
    tx.run(max_iterations=600)
    elapsed = clock.time() - t_start

    assert elapsed > 0.0
    measured_hz = tx.sent / elapsed
    # 60 Hz +- 25 %, per the frozen row.
    assert 45.0 <= measured_hz <= 75.0, f"measured {measured_hz:.1f} Hz"
    # and, since the clock is exact here, the rate is exactly 60:
    # 600 iterations x (1/60) s = 10.0 s of clock.
    assert elapsed == pytest.approx(10.0, abs=1e-9)
    assert tx.sent == 600


def test_sequence_numbers_are_strictly_increasing(udp_sink, clock,
                                                  loopback_socket):
    """P-01.01b-2. Sequence numbers strictly increasing with no gaps, so a
    receiver can detect loss and reorder. [a: D1]
    """
    sink = udp_sink(ports.HOTAS_TO_PCS)
    tx = transmitter(StubDevice(), clock, loopback_socket)
    tx.run(max_iterations=20)
    assert sink.wait_for(20, timeout=3.0)
    seqs = [messages.decode(d).seq for d in sink.datagrams[:20]]
    assert seqs == list(range(20))
    tx.close()


# ===========================================================================
# P-01.01b-3 -- axis indices
# ===========================================================================
def test_axis_indices_match_the_legacy_transmitter(clock, loopback_socket):
    """P-01.01b-3. roll <- axis 0; pitch <- axis 1 negated; throttle <- axis 2
    normalized; yaw <- the larger-deflection candidate. [a: Sec.4.4] [a: D7]
    """
    # distinct values, all outside the deadzone, so a swapped index shows up
    device = StubDevice(axes=[0.10, 0.20, 0.30, 0.40, 0.15])
    tx = transmitter(device, clock, loopback_socket)
    tx.iterate()

    reading = device.poll()
    assert reading.roll == pytest.approx(0.10)
    assert reading.pitch == pytest.approx(-0.20)      # axis 1, negated
    # axis 2 = 0.30 -> (1 - 0.30) / 2 = 0.35
    assert reading.throttle == pytest.approx(0.35)
    # |0.40| > |0.15| -> axis 3 wins
    assert reading.yaw == pytest.approx(0.40)
    tx.close()


def test_the_axis_indices_are_the_ones_section_4_4_states():
    """P-01.01b-3. The constants themselves, against Sec.4.4. [a: Sec.4.4]"""
    assert hotas.AXIS_ROLL == S44_AXIS_ROLL == 0
    assert hotas.AXIS_PITCH == S44_AXIS_PITCH == 1
    assert hotas.AXIS_THROTTLE == S44_AXIS_THROTTLE == 2
    assert hotas.AXIS_YAW_CANDIDATES == S44_AXIS_YAW_CANDIDATES == (3, 4)


# ===========================================================================
# P-01.01b-4 -- throttle normalization, hand computed
# ===========================================================================
def test_throttle_normalization_hand_computed():
    """P-01.01b-4. Sec.4.4 gives `throttle = (1.0 - throttle_raw) / 2.0`.

    Hand computation, written out:
        raw = +1.0  ->  (1.0 - ( 1.0)) / 2.0 = 0.0 / 2.0 = 0.0
        raw =  0.0  ->  (1.0 - ( 0.0)) / 2.0 = 1.0 / 2.0 = 0.5
        raw = -1.0  ->  (1.0 - (-1.0)) / 2.0 = 2.0 / 2.0 = 1.0
        raw = -0.5  ->  (1.0 - (-0.5)) / 2.0 = 1.5 / 2.0 = 0.75
        raw = +0.5  ->  (1.0 - ( 0.5)) / 2.0 = 0.5 / 2.0 = 0.25
    [a: Sec.4.4]
    """
    assert hotas.normalize_throttle(+1.0) == pytest.approx(0.0)
    assert hotas.normalize_throttle(0.0) == pytest.approx(0.5)
    assert hotas.normalize_throttle(-1.0) == pytest.approx(1.0)
    assert hotas.normalize_throttle(-0.5) == pytest.approx(0.75)
    assert hotas.normalize_throttle(+0.5) == pytest.approx(0.25)


def test_throttle_is_clamped_into_the_d1_range():
    """P-01.01b-4. A physical axis can over-travel; D1 fixes throttle in [0, 1],
    so the normalization clamps rather than emitting a value D1 would refuse.
    [b: D1] [a: Sec.4.4]
    """
    assert hotas.normalize_throttle(-1.4) == 1.0
    assert hotas.normalize_throttle(+1.4) == 0.0


# ===========================================================================
# P-01.01b-5 -- deadzone
# ===========================================================================
def test_deadzone_suppresses_small_deflections():
    """P-01.01b-5. Sec.4.4's comparison is `abs(raw) > 0.05`, so exactly +-0.05
    is suppressed and +-0.06 passes through unchanged. [a: Sec.4.4]
    """
    assert hotas.DEADZONE == S44_DEADZONE == 0.05
    for v in (0.05, -0.05, 0.0, 0.049):
        assert hotas.apply_deadzone(v) == 0.0, v
    assert hotas.apply_deadzone(0.06) == pytest.approx(0.06)
    assert hotas.apply_deadzone(-0.06) == pytest.approx(-0.06)
    # and the value is passed through, not rescaled
    assert hotas.apply_deadzone(0.9) == pytest.approx(0.9)


# ===========================================================================
# D7 yaw selection -- the check R-4 names explicitly
# ===========================================================================
def test_yaw_selects_the_larger_deflection_of_axes_three_and_four():
    """P-01.01b-3 / R-4's D7 yaw-selection check.
    [a: D7] "HOTAS yaw axis: the larger deflection of candidate axes 3 and 4."
    """
    assert hotas.select_yaw(0.8, 0.2) == pytest.approx(0.8)
    assert hotas.select_yaw(0.2, 0.8) == pytest.approx(0.8)
    # "larger deflection" is magnitude, so a large negative beats a small
    # positive -- and keeps its sign.
    assert hotas.select_yaw(-0.8, 0.2) == pytest.approx(-0.8)
    assert hotas.select_yaw(0.2, -0.8) == pytest.approx(-0.8)
    # the deadzone still applies to the winner
    assert hotas.select_yaw(0.04, 0.03) == 0.0


def test_yaw_selection_is_stable_when_both_axes_are_equal():
    """R-4's D7 yaw-selection check, tie case.

    With equal magnitudes the legacy expression
    `axis3 if abs(axis3) > abs(axis4) else axis4` takes axis 4. Asserting the
    tie pins the choice so it cannot alternate frame to frame. [a: Sec.4.4]
    """
    assert hotas.select_yaw(0.5, 0.5) == pytest.approx(0.5)
    assert hotas.select_yaw(0.5, -0.5) == pytest.approx(-0.5)   # axis 4 wins
    assert hotas.select_yaw(-0.5, 0.5) == pytest.approx(0.5)    # axis 4 wins
    # repeated evaluation is identical
    assert [hotas.select_yaw(0.5, -0.5) for _ in range(5)] == [-0.5] * 5


# ===========================================================================
# P-01.01b-6 -- raw switch states on the wire
# ===========================================================================
def test_switch_states_are_raw_on_the_wire(udp_sink, clock, loopback_socket):
    """P-01.01b-6. [a: D7] "The transmitter puts raw pressed states on the wire;
    toggle edges for btn2 and btn4 are detected by the receiver." So a held
    button reads True in every datagram, not once.
    """
    sink = udp_sink(ports.HOTAS_TO_PCS)
    device = StubDevice(buttons={2: True, 4: True, 6: True})
    tx = transmitter(device, clock, loopback_socket)
    tx.run(max_iterations=3)
    assert sink.wait_for(3)
    for data in sink.datagrams[:3]:
        msg = messages.decode(data)
        assert msg.switches["2"] is True
        assert msg.switches["4"] is True
        assert msg.switches["6"] is True
    tx.close()


def test_released_switch_reads_false(udp_sink, clock, loopback_socket):
    """P-01.01b-6. Release is reported as state, not as an edge. [a: D7]"""
    sink = udp_sink(ports.HOTAS_TO_PCS)
    device = StubDevice(buttons={4: True})
    tx = transmitter(device, clock, loopback_socket)
    tx.run(max_iterations=1)
    device.buttons[4] = False
    tx.run(max_iterations=1)
    assert sink.wait_for(2)
    assert messages.decode(sink.datagrams[0]).switches["4"] is True
    assert messages.decode(sink.datagrams[1]).switches["4"] is False
    tx.close()


# ===========================================================================
# P-01.01b-7 -- no traffic after teardown
# ===========================================================================
def test_no_traffic_after_stop(udp_sink, clock, loopback_socket):
    """P-01.01b-7. R-4 acceptance: "no traffic on 5005 after teardown."
    [a: D15] "Every spawned thread is stoppable (stop_event)."
    """
    sink = udp_sink(ports.HOTAS_TO_PCS)
    device = StubDevice()
    tx = transmitter(device, clock, loopback_socket)
    stop = threading.Event()

    thread = threading.Thread(target=tx.run, kwargs={"stop_event": stop},
                              name="hotas-tx")
    thread.start()
    assert sink.wait_for(1, timeout=3.0)
    stop.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "the loop did not stop on stop_event"
    tx.close()

    sink.datagrams.clear()
    # a window at least five transmit periods long
    import time as _time
    _time.sleep(5 * hotas.TRANSMIT_PERIOD_S + 0.05)
    assert sink.datagrams == [], "traffic on 5005 after teardown"


# ===========================================================================
# P-01.01b-8 -- lazy pygame import
# ===========================================================================
def test_module_imports_without_pygame():
    """P-01.01b-8. R-4: "Import pygame lazily so the module stays importable
    from .venv_fsim; the suite asserts this."

    .venv_fsim has no pygame [a: Sec.5.2], so importing this module must not
    import it. The module is already imported by this test file; the assertion
    is that pygame did not come with it.
    """
    assert "flightsim.hotas_standalone" in sys.modules
    assert "pygame" not in sys.modules, (
        "importing flightsim.hotas_standalone pulled in pygame; it must be lazy"
    )
    # and the module is usable without it
    assert hotas.normalize_throttle(0.0) == 0.5
    assert hotas.DemoDeviceSource().open() is True


def test_pygame_is_not_a_module_level_import():
    """P-01.01b-8. Evidence from the source itself, so the row does not depend
    on import order elsewhere in the session. [a: R-4]
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(hotas))
    module_level = []
    for node in tree.body:                      # top level only
        if isinstance(node, ast.Import):
            module_level += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module_level.append(node.module or "")
    assert not any("pygame" in m for m in module_level), module_level


# ===========================================================================
# P-01.01b-9 / P-01.01b-10 -- hot plug
# ===========================================================================
def test_starts_with_no_device_and_resumes_on_attach(clock, loopback_socket):
    """P-01.01b-9. R-4: "hot-plug tolerance -- start without a device and wait
    ... resume on reconnect with a 1 Hz rescan fallback."
    """
    device = StubDevice(present=False)
    tx = transmitter(device, clock, loopback_socket)

    # absent: iterating raises nothing and sends nothing
    for _ in range(10):
        assert tx.iterate() is False
        clock.advance(hotas.TRANSMIT_PERIOD_S)
    assert tx.sent == 0

    # the device appears; within two rescan periods the loop picks it up
    device.present = True
    sent_before = tx.sent
    for _ in range(int(2 * hotas.RESCAN_PERIOD_S / hotas.TRANSMIT_PERIOD_S) + 2):
        tx.iterate()
        clock.advance(hotas.TRANSMIT_PERIOD_S)
    assert tx.sent > sent_before, "did not resume after the device appeared"
    tx.close()


def test_rescan_is_at_one_hertz(clock, loopback_socket):
    """P-01.01b-9. The rescan fallback rate, measured against the row's clock.
    [a: R-4]
    """
    assert hotas.RESCAN_HZ == 1.0
    device = StubDevice(present=False)
    tx = transmitter(device, clock, loopback_socket)

    # 3 seconds of clock at the transmit period
    iterations = int(3.0 / hotas.TRANSMIT_PERIOD_S)
    for _ in range(iterations):
        tx.iterate()
        clock.advance(hotas.TRANSMIT_PERIOD_S)
    # open() is attempted once per rescan period, not once per transmit period
    assert 3 <= device_open_attempts(device) <= 4, device_open_attempts(device)
    tx.close()


def device_open_attempts(device: StubDevice) -> int:
    """Opens attempted on an absent device are counted by the stub."""
    return device.open_attempts


def test_device_removal_suspends_and_reattach_resumes(clock, loopback_socket):
    """P-01.01b-10. R-4: "suspend on JOYDEVICEREMOVED, resume on reconnect."

    The stub reports removal the way the pygame source does: `poll()` returns
    None and the source marks itself disconnected.
    """
    device = StubDevice()
    tx = transmitter(device, clock, loopback_socket)

    tx.iterate()
    assert tx.sent == 1

    device.removed = True
    assert tx.iterate() is False
    assert tx.sent == 1
    assert device.connected is False

    device.removed = False
    for _ in range(int(2 * hotas.RESCAN_PERIOD_S / hotas.TRANSMIT_PERIOD_S) + 2):
        clock.advance(hotas.TRANSMIT_PERIOD_S)
        tx.iterate()
    assert tx.sent > 1, "did not resume after re-attach"
    tx.close()


# ===========================================================================
# Debug printout [a: Sec.4.4]
# ===========================================================================
def test_debug_printout_suppresses_unchanged_axes():
    """Sec.4.4 is authoritative for "the debug-printout format including its
    on-change suppression". Reported in docs/MODEL_INTAKE.md F-6: the supplied
    file declares the memory variables but never reads them. This row pins the
    mechanism the file names. [b: Sec.4.4]
    """
    import io

    out = io.StringIO()
    printer = hotas.DebugPrinter(out=out, axes=True)
    r1 = hotas.Reading(roll=0.5, pitch=0.0, yaw=0.0, throttle=0.5, switches={})
    printer.on_reading(r1)
    first = out.getvalue()
    assert "[AXES]" in first

    printer.on_reading(r1)                 # identical: nothing new printed
    assert out.getvalue() == first

    r2 = hotas.Reading(roll=0.9, pitch=0.0, yaw=0.0, throttle=0.5, switches={})
    printer.on_reading(r2)                 # changed: printed again
    assert out.getvalue() != first
    assert out.getvalue().count("[AXES]") == 2


def test_debug_printout_reproduces_the_legacy_strings():
    """The strings the supplied file does contain, verbatim. [a: Sec.4.4]"""
    assert hotas.DebugPrinter.NO_DEVICE == "No USB joystick detected."
    assert hotas.DebugPrinter.CONNECTED == "HOTAS Connected: {name}"
    assert hotas.DebugPrinter.COUNTS == "Detected {buttons} Buttons and {axes} Axes.\n"
    assert hotas.DebugPrinter.AIRBRAKE_TOGGLE == "[CMD] Airbrake Toggle Sent"


def test_airbrake_press_edge_prints_the_legacy_message():
    """Sec.4.4's console edge. The wire still carries raw state. [a: Sec.4.4, D7]"""
    import io

    out = io.StringIO()
    printer = hotas.DebugPrinter(out=out)
    printer.on_reading(hotas.Reading(switches={"4": False}))
    assert "Airbrake" not in out.getvalue()
    printer.on_reading(hotas.Reading(switches={"4": True}))
    assert hotas.DebugPrinter.AIRBRAKE_TOGGLE in out.getvalue()
    before = out.getvalue()
    printer.on_reading(hotas.Reading(switches={"4": True}))   # held
    assert out.getvalue() == before


# ===========================================================================
# CLI surface [a: R-4]
# ===========================================================================
def test_cli_accepts_the_four_required_flags():
    """R-4: "Include --demo (hardware-free synthetic source), --debug,
    --debug-axes, --list".
    """
    p = hotas.build_parser()
    args = p.parse_args(["--demo", "--debug", "--debug-axes", "--list"])
    assert args.demo and args.debug and args.debug_axes and args.list


def test_list_without_pygame_reports_the_environment_not_a_traceback(capsys):
    """`--list` in an environment without pygame must explain, not crash.
    [b: R-4, Sec.5.2]
    """
    if "pygame" in sys.modules:
        pytest.skip("pygame is present in this environment")
    import io

    out = io.StringIO()
    rc = hotas.list_devices(out=out)
    assert rc == 2
    assert "pygame is not installed" in out.getvalue()
    assert ".venv_hotas" in out.getvalue()


def test_demo_source_is_deterministic_under_a_controlled_clock(clock):
    """The synthetic source must be predictable, or a row that uses it is
    asserting on noise. [b: D18]
    """
    a = hotas.DemoDeviceSource(clock=clock.monotonic)
    a.open()
    r1 = a.poll()
    clock.advance(1.0)
    r2 = a.poll()

    b = hotas.DemoDeviceSource(clock=clock.monotonic)
    # replay the same clock offsets against a fresh source
    clock2_values = []
    for r in (r1, r2):
        clock2_values.append((r.roll, r.pitch, r.yaw, r.throttle))
    assert clock2_values[0] != clock2_values[1]      # it does move
    assert all(-1.0 <= v <= 1.0 for v in clock2_values[0][:3])
    assert 0.0 <= clock2_values[0][3] <= 1.0
    del b


# ===========================================================================
# P-01.06d-2 / P-01.06e-2 -- receiver-side toggle edges
#
# [a: D7] "The transmitter puts raw pressed states on the wire; toggle edges
# for btn2 and btn4 are detected by the receiver; a held button does not
# re-toggle." The transmitter half is above; this is the receiver half, in
# flightsim/pcs/controls.py.
# ===========================================================================
def _controls(seq=0, switches=None):
    return HotasControls(seq=seq, roll=0.0, pitch=0.0, yaw=0.0, throttle=0.0,
                         switches={str(k): bool(v)
                                   for k, v in (switches or {}).items()})


def test_button_four_edge_toggles_air_brake_at_the_receiver():
    """P-01.06d-2. [a: D7] btn4 = air-brake toggle, edge-detected at the
    receiver, and "a held button does not re-toggle".
    [a: L2SYS-REQ-01.06d]
    """
    from flightsim.pcs.controls import BTN_AIR_BRAKE, SwitchEdgeDetector

    assert BTN_AIR_BRAKE == 4
    det = SwitchEdgeDetector()
    assert det.state.air_brake is False

    # three datagrams with the button HELD: one toggle, not three
    for seq in range(3):
        state = det.apply(_controls(seq, {4: True}))
    assert state.air_brake is True
    assert det.toggles == 1, "a held button re-toggled"

    # release: no toggle
    state = det.apply(_controls(3, {4: False}))
    assert state.air_brake is True
    assert det.toggles == 1

    # a new press: toggles back
    state = det.apply(_controls(4, {4: True}))
    assert state.air_brake is False
    assert det.toggles == 2


def test_button_two_edge_toggles_gear_at_the_receiver():
    """P-01.06e-2. [a: D7] btn2 = landing-gear toggle, edge-detected at the
    receiver. [a: L2SYS-REQ-01.06e]
    """
    from flightsim.pcs.controls import BTN_GEAR, SwitchEdgeDetector

    assert BTN_GEAR == 2
    det = SwitchEdgeDetector()
    assert det.state.gear_down is True          # [a: D8] gear down at t0

    for seq in range(3):
        state = det.apply(_controls(seq, {2: True}))
    assert state.gear_down is False
    assert det.toggles == 1

    det.apply(_controls(3, {2: False}))
    state = det.apply(_controls(4, {2: True}))
    assert state.gear_down is True
    assert det.toggles == 2


def test_button_six_is_a_momentary_wheel_brake():
    """[a: D7] "btn6 = momentary wheel brake" -- a level, not a toggle, so it
    releases when the button does.
    """
    from flightsim.pcs.controls import BTN_WHEEL_BRAKE, SwitchEdgeDetector

    assert BTN_WHEEL_BRAKE == 6
    det = SwitchEdgeDetector()
    assert det.wheel_brake == 0.0
    det.apply(_controls(0, {6: True}))
    assert det.wheel_brake == 1.0
    det.apply(_controls(1, {6: True}))
    assert det.wheel_brake == 1.0, "a held momentary brake must stay applied"
    det.apply(_controls(2, {6: False}))
    assert det.wheel_brake == 0.0, "the momentary brake must release"
    assert det.toggles == 0, "btn6 must not toggle anything"


def test_the_transmitter_and_the_receiver_agree_end_to_end(clock,
                                                           loopback_socket,
                                                           udp_sink):
    """P-01.01b-6 x P-01.06d-2. The transmitter sends raw state and the
    receiver turns it into one toggle -- the two halves of D7, joined over the
    real port. [a: D7]
    """
    from flightsim.pcs.controls import SwitchEdgeDetector

    sink = udp_sink(ports.HOTAS_TO_PCS)
    device = StubDevice(buttons={2: True, 4: True})
    tx = transmitter(device, clock, loopback_socket)
    tx.run(max_iterations=4)                     # four datagrams, button held
    assert sink.wait_for(4)

    det = SwitchEdgeDetector()
    for data in sink.datagrams[:4]:
        det.apply(messages.decode(data))

    assert det.toggles == 2, "one toggle per button, however many datagrams"
    assert det.state.gear_down is False
    assert det.state.air_brake is True
    tx.close()
