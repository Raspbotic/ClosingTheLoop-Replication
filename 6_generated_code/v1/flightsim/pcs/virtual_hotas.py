"""
flightsim/pcs/virtual_hotas.py
==============================
On-screen virtual flight controls (stick, throttle, pedals).

TRACEABILITY
------------
[a] L2SYS-REQ-01.08b: "The PCS shall provide on-screen virtual flight
    controls (stick, throttle, pedals) usable when the physical HOTAS is
    absent; physical HOTAS input shall take precedence when present."
    Allocated to PilotControlStation (R2F_L2 matrix, L2SYS-FCN-01.8).
[a] TA action controlManualFlight, inside controlUserInputs of
    UserInterfaceManager (fsim_TA_PCS_ThreadFlightSim_UIM.sysml).
[a: Decision D10] "Virtual HOTAS (01.08b): physical precedence window
    1.0 s (fresh 5005 datagram wins; widget greys out with title change);
    stick and pedals spring to neutral on ANY mouse release (override
    mouseReleaseEvent -- the sliderReleased signal misses groove clicks);
    on physical loss, stick/pedals center while throttle retains last
    position (bumpless); widget mirrors live physical commands at 10 Hz
    while locked; throttle slider painted with scale IDLE 0 / 25 / 50 /
    75 / MIL 100 and AB marker at top of travel; drag-down = stick back =
    pitch +1 (matches physical sign)."
[a: D14] "Virtual HOTAS left" in the PCS layout; reference screenshot
    Desired_UIUX_capture.JPG shows the Throttle column and the
    "FBW Stick  Pedals" group.
"""

from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from flightsim.common.messages import HotasControls

#: [a: D10] "physical precedence window 1.0 s".
PHYSICAL_PRECEDENCE_S = 1.0

#: [a: D10] "widget mirrors live physical commands at 10 Hz while locked".
MIRROR_HZ = 10.0

#: [a: D10] throttle scale labels.
THROTTLE_SCALE = ((0, "IDLE 0"), (25, "25"), (50, "50"), (75, "75"),
                  (100, "MIL 100"))


class StickPad(QtWidgets.QWidget):
    """Two-axis force-feel stick. [a] 01.08b "stick".

    [a: D10] "drag-down = stick back = pitch +1 (matches physical sign)";
    springs to neutral on ANY mouse release.
    """

    changed = QtCore.pyqtSignal(float, float)          # roll, pitch

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self.roll = 0.0
        self.pitch = 0.0
        self._mirroring = False

    # -- input -----------------------------------------------------------
    def _set_from_pos(self, pos: QtCore.QPoint) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        roll = (pos.x() - w / 2.0) / (w / 2.0)
        # [a: D10] dragging DOWN is stick back, i.e. pitch +1
        pitch = (pos.y() - h / 2.0) / (h / 2.0)
        self.roll = max(-1.0, min(1.0, roll))
        self.pitch = max(-1.0, min(1.0, pitch))
        self.changed.emit(self.roll, self.pitch)
        self.update()

    def mousePressEvent(self, event):                   # noqa: N802
        if self.isEnabled():
            self._set_from_pos(event.pos())

    def mouseMoveEvent(self, event):                    # noqa: N802
        if self.isEnabled() and (event.buttons() & QtCore.Qt.LeftButton):
            self._set_from_pos(event.pos())

    def mouseReleaseEvent(self, event):                 # noqa: N802
        """[a: D10] spring to neutral on ANY mouse release. Overridden
        rather than connected to a release signal, because a click on the
        groove/background would otherwise never produce one."""
        self.center()

    def center(self) -> None:
        """[a: D10] "on physical loss, stick/pedals center"."""
        self.roll = 0.0
        self.pitch = 0.0
        self.changed.emit(0.0, 0.0)
        self.update()

    def mirror(self, roll: float, pitch: float) -> None:
        """[a: D10] "widget mirrors live physical commands ... while
        locked"."""
        self.roll = float(roll)
        self.pitch = float(pitch)
        self.update()

    # -- rendering -------------------------------------------------------
    def paintEvent(self, event):                        # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        radius = min(w, h) / 2.0 - 6.0
        colour = QtGui.QColor(60, 230, 60) if self.isEnabled() \
            else QtGui.QColor(120, 120, 120)
        p.setPen(QtGui.QPen(QtGui.QColor(140, 140, 140), 1))
        p.setBrush(QtGui.QBrush(QtGui.QColor(10, 12, 10)))
        p.drawEllipse(QtCore.QPointF(w / 2.0, h / 2.0), radius, radius)
        p.setPen(QtGui.QPen(colour, 2))
        p.setBrush(QtGui.QBrush(colour))
        p.drawEllipse(QtCore.QPointF(w / 2.0 + self.roll * radius,
                                     h / 2.0 + self.pitch * radius), 8.0, 8.0)
        p.end()


class ThrottleSlider(QtWidgets.QSlider):
    """Throttle lever. [a] 01.08b "throttle".

    [a: D10] "throttle slider painted with scale IDLE 0 / 25 / 50 / 75 /
    MIL 100 and AB marker at top of travel". It does NOT spring back: the
    throttle retains its last position (bumpless transfer).
    """

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Vertical, parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.setMinimumHeight(240)
        self.setMinimumWidth(70)

    def norm(self) -> float:
        return self.value() / 100.0

    def paintEvent(self, event):                        # noqa: N802
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QPen(QtGui.QColor(190, 190, 190), 1))
        font = p.font()
        font.setPointSize(7)
        p.setFont(font)
        h = self.height()
        for pct, label in THROTTLE_SCALE:
            y = int(h - 8 - (h - 16) * pct / 100.0)
            p.drawLine(2, y, 10, y)
            p.drawText(13, y + 3, label)
        # [a: D10] AB marker at the top of travel
        p.setPen(QtGui.QPen(QtGui.QColor(230, 120, 40), 2))
        p.drawLine(2, 4, self.width() - 2, 4)
        p.drawText(13, 14, "AB")
        p.end()


class PedalSlider(QtWidgets.QSlider):
    """Rudder pedals. [a] 01.08b "pedals".

    [a: D10] springs to neutral on ANY mouse release, via an override of
    mouseReleaseEvent (the sliderReleased signal misses groove clicks).
    """

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.setRange(-100, 100)
        self.setValue(0)

    def norm(self) -> float:
        return self.value() / 100.0

    def mouseReleaseEvent(self, event):                 # noqa: N802
        super().mouseReleaseEvent(event)
        self.center()                                   # [a: D10]

    def center(self) -> None:
        self.setValue(0)


class VirtualHotasWidget(QtWidgets.QGroupBox):
    """The Virtual HOTAS panel of the PCS.

    [a] L2SYS-REQ-01.08b in full: usable when the physical HOTAS is
    absent, and yielding to the physical HOTAS when present [a: D10].
    """

    TITLE_VIRTUAL = "Virtual HOTAS - ACTIVE"
    TITLE_LOCKED = "Virtual HOTAS - LOCKED (physical HOTAS in control)"

    def __init__(self, parent=None):
        super().__init__(self.TITLE_VIRTUAL, parent)
        self.physical_active = False
        self._last_physical: Optional[HotasControls] = None
        self._mirror_due = 0.0

        outer = QtWidgets.QHBoxLayout(self)

        thr_col = QtWidgets.QVBoxLayout()
        thr_col.addWidget(QtWidgets.QLabel("Throttle"))
        self.throttle = ThrottleSlider()
        thr_col.addWidget(self.throttle)
        outer.addLayout(thr_col)

        stick_col = QtWidgets.QVBoxLayout()
        stick_col.addWidget(QtWidgets.QLabel("FBW Stick / Pedals"))
        self.stick = StickPad()
        stick_col.addWidget(self.stick)
        self.pedals = PedalSlider()
        stick_col.addWidget(self.pedals)
        self.pedal_brake = QtWidgets.QPushButton("PEDAL BRAKE")
        self.pedal_brake.setCheckable(True)
        stick_col.addWidget(self.pedal_brake)
        outer.addLayout(stick_col)

    # -- output ----------------------------------------------------------
    def controls(self) -> HotasControls:
        """The virtual control command. [a] 01.08b."""
        return HotasControls(roll=self.stick.roll, pitch=self.stick.pitch,
                             yaw=self.pedals.norm(),
                             throttle=self.throttle.norm(),
                             switches={"btn6": self.pedal_brake.isChecked()})

    # -- physical precedence ---------------------------------------------
    def set_physical_active(self, active: bool) -> None:
        """[a] 01.08b "physical HOTAS input shall take precedence when
        present"; [a: D10] "widget greys out with title change" and, on
        loss, "stick/pedals center while throttle retains last position
        (bumpless)"."""
        active = bool(active)
        if active == self.physical_active:
            return
        self.physical_active = active
        self.setTitle(self.TITLE_LOCKED if active else self.TITLE_VIRTUAL)
        for widget in (self.stick, self.pedals, self.throttle,
                       self.pedal_brake):
            widget.setEnabled(not active)
        if not active:
            # [a: D10] bumpless hand-back: centre the springy axes, keep
            # the throttle where the physical lever left it.
            self.stick.center()
            self.pedals.center()
        self.update()

    def mirror_physical(self, controls: HotasControls, now: float) -> bool:
        """[a: D10] "widget mirrors live physical commands at 10 Hz while
        locked". Returns True when the mirror actually refreshed."""
        self._last_physical = controls
        if not self.physical_active or now < self._mirror_due:
            return False
        self._mirror_due = now + 1.0 / MIRROR_HZ
        self.stick.mirror(controls.roll, controls.pitch)
        self.pedals.setValue(int(round(controls.yaw * 100)))
        self.throttle.setValue(int(round(controls.throttle * 100)))
        return True


__all__ = ["VirtualHotasWidget", "StickPad", "ThrottleSlider", "PedalSlider",
           "PHYSICAL_PRECEDENCE_S", "MIRROR_HZ", "THROTTLE_SCALE"]
