"""Virtual HOTAS (stick, throttle, pedals) and physical-precedence logic.
Traces: L2SYS-REQ-01.08b -- "on-screen virtual flight controls (stick,
throttle, pedals) usable when the physical HOTAS is absent; physical
HOTAS input shall take precedence when present."""
import socket, time, pytest
from flightsim.common import ports
from flightsim.common.messages import HotasControls


@pytest.fixture(scope="module")
def pcs(qapp):
    from flightsim.pcs.main_window import MainWindow
    from conftest import process_events
    win = MainWindow()
    win.show()
    process_events(qapp, 4.0)
    yield win
    win.close()


def test_widget_axis_mapping(qapp, pcs):
    """[01.08b] stick/pedals/throttle map to the same normalized ranges
    as the physical path; pedal brake maps to the btn6 channel."""
    vh = pcs.virtual_hotas
    vh.stick.set_axes(0.5, -0.25)
    vh.sl_pedals.setValue(40)
    vh.sl_throttle.setValue(80)
    vh._set_brake(True)
    c = vh.read()
    assert abs(c.roll - 0.5) < 1e-9
    assert abs(c.pitch - (-0.25)) < 1e-9
    assert abs(c.yaw - 0.4) < 1e-9
    assert abs(c.throttle - 0.8) < 1e-9
    assert c.switches.get("btn6") is True
    vh._set_brake(False)
    vh.stick.center(); vh.sl_pedals.setValue(0); vh.sl_throttle.setValue(0)


def test_stick_springs_back_on_release(qapp, pcs):
    """[c: FBW centering] release returns stick to neutral."""
    vh = pcs.virtual_hotas
    vh.stick.set_axes(0.9, 0.9)
    vh.stick.mouseReleaseEvent(None)
    c = vh.read()
    assert c.roll == 0.0 and c.pitch == 0.0


def test_virtual_feeds_physics_when_no_physical(qapp, pcs):
    """[01.08b] with no physical datagrams, the virtual values become the
    active control source and reach the FDM input mapping."""
    from conftest import process_events
    vh = pcs.virtual_hotas
    # ensure physical is stale
    pcs.io._last_phys_rx = 0.0
    vh.stick.set_axes(0.33, 0.1)
    vh.sl_throttle.setValue(70)
    process_events(qapp, 0.6)
    assert not pcs.io.physical_active
    assert abs(pcs.io.last_controls.roll - 0.33) < 1e-6
    assert abs(pcs.io.last_controls.throttle - 0.70) < 1e-6
    # reached JSBSim (autopilot off -> passthrough)
    assert abs(pcs.physics._fdm["fcs/aileron-cmd-norm"] - 0.33) < 0.02
    assert vh.isEnabled(), "widget must not be greyed (no physical input)"
    assert vh.title() == vh.TITLE_ACTIVE
    vh.stick.center(); vh.sl_throttle.setValue(0)


def test_physical_precedence_and_fallback(qapp, pcs):
    """[01.08b] a fresh physical datagram overrides the virtual source;
    after PHYSICAL_TIMEOUT_S of silence control falls back to virtual,
    and the widget grey-out tracks the state."""
    from conftest import process_events
    vh = pcs.virtual_hotas
    vh.stick.set_axes(-0.5, 0.0)         # virtual says roll -0.5
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.sendto(HotasControls(seq=1, roll=0.8, throttle=0.9).to_json(),
              (ports.LOCALHOST, ports.UDP_HOTAS_CONTROL))
    process_events(qapp, 0.4)
    assert pcs.io.physical_active, "fresh datagram must claim precedence"
    assert abs(pcs.io.last_controls.roll - 0.8) < 1e-6, \
        "physical values must win over virtual"
    assert not vh.isEnabled(), "widget must grey out (physical active)"
    assert vh.title() == vh.TITLE_LOCKED
    # silence > timeout -> fallback to virtual with the transition
    # policy [c 2026-07-13]: stick/pedals center, throttle retained
    process_events(qapp, pcs.io.PHYSICAL_TIMEOUT_S + 0.4)
    assert not pcs.io.physical_active
    assert pcs.io.last_controls.roll == 0.0, \
        "stick must CENTER on fallback (spring-loaded, mirror cleared)"
    assert abs(pcs.io.last_controls.throttle - 0.9) < 0.01, \
        "throttle must RETAIN last physical position (bumpless)"
    assert vh.isEnabled() and vh.title() == vh.TITLE_ACTIVE
    tx.close()
    vh.stick.center(); vh.sl_throttle.setValue(0)


def test_pedal_brake_reaches_fdm(qapp, pcs):
    """[01.08b + wire-equivalence to btn6] held PEDAL BRAKE commands the
    wheel brakes in the FDM (parking brake released first)."""
    from conftest import process_events
    pcs.io._last_phys_rx = 0.0
    pcs.physics.set_parking_brake(False)
    vh = pcs.virtual_hotas
    vh.btn_brake.pressed.emit()
    process_events(qapp, 0.4)
    assert pcs.physics._fdm["fcs/left-brake-cmd-norm"] == 1.0
    vh.btn_brake.released.emit()
    process_events(qapp, 0.4)
    assert pcs.physics._fdm["fcs/left-brake-cmd-norm"] == 0.0
    pcs.physics.set_parking_brake(True)


def test_stick_mouse_pitch_convention(qapp, pcs):
    """[bug fix 2026-07-12] dragging the dot DOWN = stick back = pitch +1
    (nose-up), matching the physical HOTAS sign convention."""
    from PyQt5.QtCore import QPoint
    pad = pcs.virtual_hotas.stick
    pad.resize(140, 140)
    cx, cy = pad.width() // 2, pad.height() // 2
    pad._apply_pos(QPoint(cx, cy + 50))        # drag below center
    assert pad.pitch > 0.5, "down-drag must be positive pitch (stick back)"
    pad._apply_pos(QPoint(cx, cy - 50))        # drag above center
    assert pad.pitch < -0.5, "up-drag must be negative pitch (stick fwd)"
    pad.center()


def test_pedals_spring_back_on_any_release(qapp, pcs):
    """[bug fix 2026-07-13] pedals return to neutral on mouse release --
    including the groove-click path that bypasses sliderReleased."""
    vh = pcs.virtual_hotas
    vh.sl_pedals.setValue(65)               # e.g. positioned by groove click
    assert vh.sl_pedals.value() == 65
    vh.sl_pedals.mouseReleaseEvent(None)    # any release path
    assert vh.sl_pedals.value() == 0, "pedals must spring back to neutral"
    assert vh.read().yaw == 0.0


def test_widget_mirrors_physical_hotas(qapp, pcs):
    """[customer request 2026-07-13] with physical input active, the
    greyed widget follows the live commands (stick dot, throttle,
    pedals)."""
    from conftest import process_events
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.sendto(HotasControls(seq=9, roll=0.4, pitch=-0.3, yaw=0.5,
                            throttle=0.65).to_json(),
              (ports.LOCALHOST, ports.UDP_HOTAS_CONTROL))
    process_events(qapp, 0.5)               # >= one 10 Hz display tick
    vh = pcs.virtual_hotas
    assert pcs.io.physical_active
    assert abs(vh.stick.roll - 0.4) < 0.01
    assert abs(vh.stick.pitch - (-0.3)) < 0.01
    assert vh.sl_throttle.value() == 65
    assert vh.sl_pedals.value() == 50
    tx.close()
    # let precedence lapse so later tests see the virtual source again
    process_events(qapp, pcs.io.PHYSICAL_TIMEOUT_S + 0.4)
    vh.stick.center(); vh.sl_pedals.setValue(0); vh.sl_throttle.setValue(0)