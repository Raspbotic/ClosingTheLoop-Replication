"""Virtual HOTAS widget. [a: D10] [a: L2SYS-REQ-01.08b]

[a: L2SYS-REQ-01.08b] "The PCS shall provide on-screen virtual flight controls
(stick, throttle, pedals) usable when the physical HOTAS is absent; physical
HOTAS input shall take precedence when present."

[a: D10] "Physical-precedence window 1.0 s: a fresh datagram on 5005 takes
precedence; the widget greys out and its title changes. Stick and pedals return
to neutral on any mouse release. On loss of the physical device, stick and
pedals centre and the throttle retains its last position. The widget mirrors
live physical commands at 10 Hz while locked; mirroring is display-only.
Throttle scale IDLE 0 / 25 / 50 / 75 / MIL 100 with an afterburner marker at the
top of travel. Drag-down = stick back = pitch +1."

Layout follows the reference screenshot's `Throttle` and `FBW Stick  Pedals`
groups, including the red-outlined `PEDAL BRAKE` button.
[a: Sec.4.3, authoritative for layout]

The geometry -- pixel position to axis command -- is in `stick_axes()` below,
Qt-free, so "drag-down = pitch +1" is verifiable without a mouse.
"""

from __future__ import annotations

from flightsim.pcs.controls import ControlSample

#: [a: D10] "Throttle scale IDLE 0 / 25 / 50 / 75 / MIL 100"
THROTTLE_TICKS = ((0, "IDLE"), (25, "25"), (50, "50"), (75, "75"), (100, "MIL"))

#: [a: D10] "with an afterburner marker at the top of travel"
AFTERBURNER_LABEL = "A/B"
AFTERBURNER_POSITION = 100

#: [a: D10] the title while the physical device holds precedence.
TITLE_FREE = "FBW Stick  Pedals"
TITLE_LOCKED = "FBW Stick  Pedals  [PHYSICAL HOTAS]"

PEDAL_BRAKE_LABEL = "PEDAL BRAKE"


def stick_axes(x_px: float, y_px: float, size_px: float) -> tuple[float, float]:
    """Stick position in pixels to (roll, pitch). [a: D10]

    The stick area is a square of `size_px`; its centre is neutral.

        roll  = (x - centre) / radius        right is +1
        pitch = (y - centre) / radius        **down is +1**

    [a: D10] "Drag-down = stick back = pitch +1" -- so pitch is **not**
    inverted here, which is the opposite of what a screen-coordinate intuition
    suggests and is exactly why the Decision states it.

    The pilot-side convention this produces is then reconciled with the FDM's
    inverted elevator once, in `PhysicsThread._write_controls` (measurement
    M-A). [a: D17]
    """
    radius = size_px / 2.0
    if radius <= 0.0:
        return 0.0, 0.0
    roll = (x_px - radius) / radius
    pitch = (y_px - radius) / radius
    return (_clip(roll, -1.0, 1.0), _clip(pitch, -1.0, 1.0))


def axes_to_stick(roll: float, pitch: float, size_px: float
                  ) -> tuple[float, float]:
    """The inverse, for drawing the knob and for mirroring. [b: D10]"""
    radius = size_px / 2.0
    return (radius + roll * radius, radius + pitch * radius)


def slider_to_axis(value: int, minimum: int, maximum: int) -> float:
    """A pedals slider position to yaw in [-1, 1]. [b: D1]"""
    span = maximum - minimum
    if span <= 0:
        return 0.0
    return _clip(2.0 * (value - minimum) / span - 1.0, -1.0, 1.0)


def slider_to_throttle(value: int, minimum: int = 0, maximum: int = 100
                       ) -> float:
    """A throttle slider position to throttle in [0, 1]. [b: D1]"""
    span = maximum - minimum
    if span <= 0:
        return 0.0
    return _clip((value - minimum) / span, 0.0, 1.0)


def _clip(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


# ---------------------------------------------------------------------------
# Qt widget
# ---------------------------------------------------------------------------
def build_virtual_hotas(on_change=None, parent=None):
    """The Throttle / FBW Stick / Pedals group. Qt is imported here only."""
    from PyQt5 import QtCore, QtGui, QtWidgets

    class StickPad(QtWidgets.QWidget):
        """The circular stick area. [a: Sec.4.3 layout] [a: D10]"""

        moved = QtCore.pyqtSignal(float, float)
        released = QtCore.pyqtSignal()

        def __init__(self, parent=None, size_px: int = 180):
            super().__init__(parent)
            self.setObjectName("stickPad")
            self.setFixedSize(size_px, size_px)
            self.size_px = size_px
            self.roll = 0.0
            self.pitch = 0.0
            self.mirrored = False

        # -- drag ------------------------------------------------------
        def _apply(self, pos) -> None:
            roll, pitch = stick_axes(pos.x(), pos.y(), self.size_px)
            self.roll, self.pitch = roll, pitch
            self.moved.emit(roll, pitch)
            self.update()

        def mousePressEvent(self, e):        # noqa: N802 - Qt naming
            if self.isEnabled():
                self._apply(e.pos())

        def mouseMoveEvent(self, e):         # noqa: N802 - Qt naming
            if self.isEnabled() and e.buttons():
                self._apply(e.pos())

        def mouseReleaseEvent(self, e):      # noqa: N802 - Qt naming
            # [a: D10] "Stick and pedals return to neutral on any mouse
            # release."
            self.centre()
            self.released.emit()

        def centre(self) -> None:
            self.roll = 0.0
            self.pitch = 0.0
            self.moved.emit(0.0, 0.0)
            self.update()

        def mirror(self, roll: float, pitch: float) -> None:
            """[a: D10] "mirroring is display-only" -- this never emits
            `moved`, so nothing it shows can reach the aircraft."""
            self.roll, self.pitch = roll, pitch
            self.mirrored = True
            self.update()

        def paintEvent(self, e):             # noqa: N802 - Qt naming
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.fillRect(self.rect(), QtGui.QColor("#000000"))
            pen = QtGui.QPen(QtGui.QColor("#7a7a7a"), 1)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            r = self.size_px / 2.0 - 2
            p.drawEllipse(QtCore.QPointF(self.size_px / 2.0,
                                         self.size_px / 2.0), r, r)
            x, y = axes_to_stick(self.roll, self.pitch, self.size_px)
            colour = "#7a7a7a" if self.mirrored or not self.isEnabled() \
                else "#00ff00"
            p.setBrush(QtGui.QColor(colour))
            p.setPen(QtGui.QPen(QtGui.QColor(colour)))
            p.drawEllipse(QtCore.QPointF(x, y), 9, 9)
            p.end()

    class VirtualHotas(QtWidgets.QWidget):
        """Throttle + stick + pedals. [a: L2SYS-REQ-01.08b] [a: D10]"""

        def __init__(self, on_change=None, parent=None):
            super().__init__(parent)
            self.setObjectName("virtualHotas")
            self._on_change = on_change
            self.locked = False
            self._build()

        def _build(self) -> None:
            row = QtWidgets.QHBoxLayout(self)

            # ---- Throttle -----------------------------------------
            self.throttle_group = QtWidgets.QGroupBox("Throttle")
            self.throttle_group.setObjectName("throttleGroup")
            tl = QtWidgets.QVBoxLayout(self.throttle_group)
            self.throttle = QtWidgets.QSlider(QtCore.Qt.Vertical)
            self.throttle.setObjectName("throttleSlider")
            self.throttle.setRange(0, 100)
            self.throttle.setValue(0)
            self.throttle.setTickPosition(QtWidgets.QSlider.TicksRight)
            self.throttle.setTickInterval(25)
            self.throttle.valueChanged.connect(self._throttle_changed)
            tl.addWidget(self.throttle)
            # [a: D10] the scale marks and the afterburner marker
            self.throttle_scale = QtWidgets.QLabel(
                " ".join(label for _, label in reversed(THROTTLE_TICKS))
            )
            self.throttle_scale.setObjectName("throttleScale")
            self.afterburner = QtWidgets.QLabel(AFTERBURNER_LABEL)
            self.afterburner.setObjectName("afterburnerMarker")
            tl.addWidget(self.afterburner)
            tl.addWidget(self.throttle_scale)
            row.addWidget(self.throttle_group)

            # ---- FBW Stick / Pedals -------------------------------
            self.stick_group = QtWidgets.QGroupBox(TITLE_FREE)
            self.stick_group.setObjectName("stickGroup")
            sl = QtWidgets.QVBoxLayout(self.stick_group)
            self.stick = StickPad(self.stick_group)
            self.stick.moved.connect(self._stick_moved)
            self.stick.released.connect(self._changed)
            sl.addWidget(self.stick, alignment=QtCore.Qt.AlignCenter)

            self.pedals = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.pedals.setObjectName("pedalsSlider")
            self.pedals.setRange(-100, 100)
            self.pedals.setValue(0)
            self.pedals.valueChanged.connect(self._pedals_changed)
            self.pedals.sliderReleased.connect(self.centre_pedals)
            sl.addWidget(self.pedals)

            self.pedal_brake = QtWidgets.QPushButton(PEDAL_BRAKE_LABEL)
            self.pedal_brake.setObjectName("pedalBrake")
            self.pedal_brake.setCheckable(True)
            self.pedal_brake.setStyleSheet(
                "QPushButton { border: 1px solid #ef4444; color: #ef4444; }"
                "QPushButton:checked { background-color: #ef4444;"
                " color: #000000; }"
            )
            self.pedal_brake.toggled.connect(lambda _=None: self._changed())
            sl.addWidget(self.pedal_brake)
            row.addWidget(self.stick_group)

        # -- reads ------------------------------------------------------
        def sample(self) -> ControlSample:
            return ControlSample(
                roll=self.stick.roll,
                pitch=self.stick.pitch,
                yaw=slider_to_axis(self.pedals.value(), -100, 100),
                throttle=slider_to_throttle(self.throttle.value()),
            )

        @property
        def wheel_brake(self) -> float:
            return 1.0 if self.pedal_brake.isChecked() else 0.0

        # -- writes -----------------------------------------------------
        def _stick_moved(self, roll: float, pitch: float) -> None:
            self._changed()

        def _pedals_changed(self, _value: int) -> None:
            self._changed()

        def _throttle_changed(self, _value: int) -> None:
            self._changed()

        def _changed(self) -> None:
            if self._on_change is not None and not self.locked:
                self._on_change(self.sample())

        def centre_pedals(self) -> None:
            """[a: D10] "Stick and pedals return to neutral on any mouse
            release.\""""
            self.pedals.setValue(0)

        def centre_stick_and_pedals(self) -> None:
            self.stick.centre()
            self.centre_pedals()

        # -- precedence lock [a: D10] ----------------------------------
        def set_locked(self, locked: bool) -> None:
            """"the widget greys out and its title changes" [a: D10]"""
            if locked == self.locked:
                return
            self.locked = locked
            self.stick.setEnabled(not locked)
            self.pedals.setEnabled(not locked)
            self.throttle.setEnabled(not locked)
            self.stick_group.setTitle(TITLE_LOCKED if locked else TITLE_FREE)
            if not locked:
                # [a: D10] on loss, stick and pedals centre; the throttle
                # retains its last position, so it is deliberately untouched.
                self.stick.mirrored = False
                self.centre_stick_and_pedals()
            self.stick.update()

        def mirror(self, sample: ControlSample) -> None:
            """[a: D10] "The widget mirrors live physical commands at 10 Hz
            while locked; mirroring is display-only." Blocks the throttle's
            signal so a mirrored position cannot be read back as a command.
            """
            if not self.locked:
                return
            self.stick.mirror(sample.roll, sample.pitch)
            self.pedals.blockSignals(True)
            self.pedals.setValue(int(round(sample.yaw * 100)))
            self.pedals.blockSignals(False)
            self.throttle.blockSignals(True)
            self.throttle.setValue(int(round(sample.throttle * 100)))
            self.throttle.blockSignals(False)

    return VirtualHotas(on_change, parent)
