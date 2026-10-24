"""On-screen virtual flight controls: stick, throttle, pedals, pedal brake.
[a: L2SYS-REQ-01.08b] [a: Behaviour B8]
[a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#controlUserInputs::controlManualFlight]
Appearance per the reference screenshot: 'Throttle' vertical slider, 'FBW Stick Pedals'
circle with a dot, horizontal pedal slider, 'PEDAL BRAKE' button.
"""
from __future__ import annotations

import math

from PyQt5 import QtCore, QtGui, QtWidgets

GREEN = QtGui.QColor(0, 220, 60)
GREY = QtGui.QColor(110, 110, 110)


class StickWidget(QtWidgets.QWidget):
    """Circle with a dot. Drag right = roll +; drag down = stick back = pitch + [a: Behaviour B8].
    Returns to neutral on ANY mouse release [a: Behaviour B8]."""
    moved = QtCore.pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(130, 130)
        self.roll = self.pitch = 0.0
        self.locked = False
        self.setObjectName("virtual_stick")

    def radius(self):
        return min(self.width(), self.height()) / 2 - 8

    def _set_from_pos(self, pos):
        c = QtCore.QPointF(self.width() / 2, self.height() / 2)
        R = self.radius()
        x = (pos.x() - c.x()) / R
        y = (pos.y() - c.y()) / R
        m = math.hypot(x, y)
        if m > 1:
            x, y = x / m, y / m
        self.set_position(x, y, emit=True)

    def set_position(self, roll, pitch, emit=False):
        self.roll, self.pitch = max(-1.0, min(1.0, roll)), max(-1.0, min(1.0, pitch))
        self.update()
        if emit:
            self.moved.emit(self.roll, self.pitch)

    def mousePressEvent(self, ev):
        if not self.locked:
            self._set_from_pos(ev.pos())

    def mouseMoveEvent(self, ev):
        if not self.locked and ev.buttons():
            self._set_from_pos(ev.pos())

    def mouseReleaseEvent(self, ev):
        if not self.locked:
            self.set_position(0.0, 0.0, emit=True)  # spring to neutral

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c = QtCore.QPointF(self.width() / 2, self.height() / 2)
        R = self.radius()
        p.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(c, R, R)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(GREY if self.locked else GREEN)
        p.drawEllipse(QtCore.QPointF(c.x() + self.roll * R, c.y() + self.pitch * R), 9, 9)
        p.end()


class SpringSlider(QtWidgets.QSlider):
    """Pedal slider: returns to centre on ANY mouse release (override, not sliderReleased)
    [a: Behaviour B8]."""

    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.setRange(-100, 100)
        self.setValue(0)
        self.setObjectName("virtual_pedals")

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        if self.isEnabled():
            self.setValue(0)


class ThrottleSlider(QtWidgets.QSlider):
    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Vertical, parent)
        self.setRange(0, 100)  # 0 = idle, 100 = full [b: HotasControls.throttle range]
        self.setObjectName("virtual_throttle")
        self.setMinimumHeight(150)


SLIDER_QSS = """
QSlider::groove:vertical { background:#111; border:1px solid #666; width:8px; }
QSlider::handle:vertical { background:#00c040; border:1px solid #0a0; height:18px; margin:0 -12px; }
QSlider::groove:horizontal { background:#111; border:1px solid #666; height:8px; }
QSlider::handle:horizontal { background:#00c040; border:1px solid #0a0; width:18px; margin:-8px 0; }
QSlider::handle:disabled { background:#555; border:1px solid #444; }
"""


class VirtualHotas(QtCore.QObject):
    """Groups the widgets and reports their commands; lock/mirror while physical HOTAS
    has precedence. [a: Behaviour B8]"""

    def __init__(self):
        super().__init__()
        self.throttle_box = QtWidgets.QGroupBox("Throttle")
        self.stick_box = QtWidgets.QGroupBox("FBW Stick Pedals")
        self.throttle = ThrottleSlider()
        self.throttle.setStyleSheet(SLIDER_QSS)
        tl = QtWidgets.QVBoxLayout(self.throttle_box)
        tl.addWidget(self.throttle, 1, QtCore.Qt.AlignHCenter)
        self.stick = StickWidget()
        self.pedals = SpringSlider()
        self.pedals.setStyleSheet(SLIDER_QSS)
        self.btn_brake = QtWidgets.QPushButton("PEDAL BRAKE")
        self.btn_brake.setObjectName("btn_pedal_brake")
        self.btn_brake.setStyleSheet("QPushButton{color:#ff4040;border:1px solid #ff4040;background:#000;padding:4px;}"
                                     "QPushButton:pressed{background:#600;}")
        sl = QtWidgets.QVBoxLayout(self.stick_box)
        sl.addWidget(self.stick, 1)
        sl.addWidget(self.pedals)
        sl.addWidget(self.btn_brake)
        self.locked = False

    def commands(self):
        """Virtual commands on the wire convention."""
        return dict(roll=self.stick.roll, pitch=self.stick.pitch, yaw=self.pedals.value() / 100.0,
                    throttle=self.throttle.value() / 100.0, wheel_brake=self.btn_brake.isDown())

    def set_locked(self, locked):
        """Physical precedence: lock, grey, retitle. On release, stick and pedals centre and
        the throttle keeps its (mirrored) position — bumpless. [a: Behaviour B8]"""
        if locked == self.locked:
            return
        self.locked = locked
        self.stick.locked = locked
        for w in (self.throttle, self.pedals):
            w.setEnabled(not locked)
        self.stick_box.setTitle("FBW Stick Pedals [HOTAS]" if locked else "FBW Stick Pedals")
        # throttle box keeps its title (72 px wide); its handle greys out
        if not locked:
            self.stick.set_position(0.0, 0.0)
            self.pedals.setValue(0)
        self.stick.update()

    def mirror(self, roll, pitch, yaw, throttle):
        """Show the live physical commands while locked."""
        self.stick.set_position(roll, pitch)
        self.pedals.blockSignals(True); self.pedals.setValue(int(round(yaw * 100))); self.pedals.blockSignals(False)
        self.throttle.blockSignals(True); self.throttle.setValue(int(round(throttle * 100))); self.throttle.blockSignals(False)
