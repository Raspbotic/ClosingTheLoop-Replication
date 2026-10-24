"""Cockpit instruments drawn as the reference screenshot shows them (round dials, ADI,
heading indicator, vertical RPM bar, attention strip) plus a HUD view.
[a: L2SYS-REQ-01.08c] Airspeed, Attitude, Fuel, Heading, RPM, HUD view, Attention Getters.
[a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#updateCockpitDisplays] [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#updateAttentionGetters]
"""
from __future__ import annotations

import math

from PyQt5 import QtCore, QtGui, QtWidgets

GREEN = QtGui.QColor(0, 230, 60)
FRAME = QtGui.QColor(170, 170, 170)
SKY = QtGui.QColor(20, 40, 110)
GROUND = QtGui.QColor(35, 35, 35)
MONO = QtGui.QFont("Courier New", 8)


class _Dial(QtWidgets.QWidget):
    def __init__(self, parent=None, size=112):
        super().__init__(parent)
        self.setMinimumSize(size, size)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)

    def circle(self):
        s = min(self.width(), self.height()) - 6
        return QtCore.QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)

    def sizeHint(self):
        return QtCore.QSize(120, 120)


class RoundGauge(_Dial):
    """Needle dial with title, primary and secondary unit readouts (screenshot style)."""

    def __init__(self, title, unit, full_scale, secondary=None, parent=None):
        super().__init__(parent)
        self.title, self.unit, self.full = title, unit, full_scale
        self.secondary = secondary  # (unit, factor)
        self.value = 0.0
        self.setObjectName(f"gauge_{title.lower()}")

    def set_value(self, v):
        if v != self.value:
            self.value = v
            self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.circle()
        p.setPen(QtGui.QPen(FRAME, 2)); p.setBrush(QtGui.QColor(0, 0, 0))
        p.drawEllipse(r)
        c = r.center()
        p.setFont(MONO)
        p.setPen(GREEN)
        p.drawText(QtCore.QRectF(r.left(), r.top() + r.height() * 0.18, r.width(), 14), QtCore.Qt.AlignCenter, self.title)
        p.drawText(QtCore.QRectF(r.left(), c.y() - 6, r.width(), 14), QtCore.Qt.AlignCenter, f"{self.value:.0f} {self.unit}")
        if self.secondary:
            u, k = self.secondary
            p.setPen(QtGui.QColor(200, 200, 200))
            p.drawText(QtCore.QRectF(r.left(), c.y() + 9, r.width(), 14), QtCore.Qt.AlignCenter, f"{self.value * k:.0f} {u}")
        frac = max(0.0, min(1.0, self.value / self.full)) if self.full else 0.0
        ang = math.radians(225 - 270 * frac)  # 7:30 -> 4:30 sweep
        L = r.width() * 0.42
        p.setPen(QtGui.QPen(GREEN, 3))
        p.drawLine(c, QtCore.QPointF(c.x() + L * math.cos(ang), c.y() - L * math.sin(ang)))
        p.end()


class AttitudeIndicator(_Dial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pitch = self.roll = 0.0
        self.setObjectName("gauge_attitude")

    def set_attitude(self, pitch, roll):
        if (pitch, roll) != (self.pitch, self.roll):
            self.pitch, self.roll = pitch, roll
            self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.circle()
        path = QtGui.QPainterPath(); path.addEllipse(r)
        p.setClipPath(path)
        c = r.center()
        p.translate(c)
        p.rotate(-self.roll)
        off = self.pitch * r.height() / 60.0
        big = r.width() * 2
        p.fillRect(QtCore.QRectF(-big, -big + off, 2 * big, big), SKY)
        p.fillRect(QtCore.QRectF(-big, off, 2 * big, big), GROUND)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
        p.drawLine(QtCore.QPointF(-big, off), QtCore.QPointF(big, off))
        p.resetTransform()
        p.setClipping(False)
        p.setPen(QtGui.QPen(GREEN, 2))
        p.drawLine(QtCore.QPointF(c.x() - 30, c.y()), QtCore.QPointF(c.x() - 8, c.y()))
        p.drawLine(QtCore.QPointF(c.x() + 8, c.y()), QtCore.QPointF(c.x() + 30, c.y()))
        p.drawEllipse(c, 3, 3)
        p.setPen(QtGui.QPen(FRAME, 2)); p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(r)
        p.end()


class HeadingIndicator(_Dial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.heading = 0.0
        self.setObjectName("gauge_heading")

    def set_heading(self, h):
        if h != self.heading:
            self.heading = h
            self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.circle()
        c = r.center()
        p.setPen(QtGui.QPen(FRAME, 2)); p.setBrush(QtGui.QColor(0, 0, 0))
        p.drawEllipse(r)
        p.setFont(MONO)
        for d in range(0, 360, 30):
            a = math.radians(d - self.heading - 90)
            R = r.width() / 2
            p.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200), 1))
            p.drawLine(QtCore.QPointF(c.x() + (R - 8) * math.cos(a), c.y() + (R - 8) * math.sin(a)),
                       QtCore.QPointF(c.x() + (R - 2) * math.cos(a), c.y() + (R - 2) * math.sin(a)))
            lab = {0: "N", 90: "E", 180: "S", 270: "W"}.get(d, str(d // 10))
            p.drawText(QtCore.QRectF(c.x() + (R - 18) * math.cos(a) - 8, c.y() + (R - 18) * math.sin(a) - 7, 16, 14), QtCore.Qt.AlignCenter, lab)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1)); p.setBrush(QtGui.QColor(255, 220, 0))
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(c.x(), c.y() - 10), QtCore.QPointF(c.x() + 7, c.y() + 6), QtCore.QPointF(c.x() - 7, c.y() + 6)]))
        box = QtCore.QRectF(c.x() - 20, r.top() - 2, 40, 14)
        p.setBrush(QtGui.QColor(0, 0, 0)); p.setPen(QtGui.QPen(GREEN, 1))
        p.drawRect(box)
        p.drawText(box, QtCore.Qt.AlignCenter, f"{self.heading % 360:03.0f}°")
        p.end()


class RpmBar(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setMinimumSize(56, 110)
        self.setObjectName("gauge_rpm")

    def set_value(self, v):
        if v != self.value:
            self.value = v
            self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0))
        p.setPen(QtGui.QPen(FRAME, 1)); p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        bar = QtCore.QRectF(self.width() / 2 - 8, 8, 16, self.height() - 40)
        p.fillRect(bar, QtGui.QColor(50, 50, 50))
        h = bar.height() * max(0.0, min(1.0, self.value / 100.0))
        p.fillRect(QtCore.QRectF(bar.left(), bar.bottom() - h, bar.width(), h), GREEN)
        p.setPen(QtGui.QColor(255, 255, 255)); p.setFont(MONO)
        p.drawText(QtCore.QRectF(0, self.height() - 30, self.width(), 14), QtCore.Qt.AlignCenter, "RPM")
        p.drawText(QtCore.QRectF(0, self.height() - 16, self.width(), 14), QtCore.Qt.AlignCenter, f"{self.value:.0f}%")
        p.end()


class HudView(QtWidgets.QWidget):
    """HUD view (01.08c): pitch ladder, horizon, speed and altitude tapes. Finding F8."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pitch = self.roll = self.heading = self.speed = self.alt = 0.0
        self.setMinimumSize(170, 110)
        self.setObjectName("gauge_hud")

    def set_state(self, pitch, roll, heading, speed_mps, alt_m):
        s = (pitch, roll, heading, speed_mps, alt_m)
        if s != (self.pitch, self.roll, self.heading, self.speed, self.alt):
            self.pitch, self.roll, self.heading, self.speed, self.alt = s
            self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(5, 12, 5))
        hud = QtGui.QColor(120, 255, 140)
        p.setPen(QtGui.QPen(hud, 1)); p.setFont(MONO)
        c = QtCore.QPointF(self.width() / 2, self.height() / 2)
        ppd = self.height() / 40.0
        p.save(); p.translate(c); p.rotate(-self.roll)
        for d in range(-30, 35, 5):
            y = (self.pitch - d) * ppd
            if abs(y) > self.height():
                continue
            w = 40 if d == 0 else 22
            p.drawLine(QtCore.QPointF(-w, y), QtCore.QPointF(-8, y)); p.drawLine(QtCore.QPointF(8, y), QtCore.QPointF(w, y))
            if d:
                p.drawText(QtCore.QPointF(w + 2, y + 4), str(d))
        p.restore()
        p.drawLine(QtCore.QPointF(c.x() - 6, c.y()), QtCore.QPointF(c.x() + 6, c.y()))
        p.drawLine(QtCore.QPointF(c.x(), c.y() - 6), QtCore.QPointF(c.x(), c.y() + 6))
        p.drawText(QtCore.QRectF(2, c.y() - 7, 44, 14), QtCore.Qt.AlignLeft, f"{self.speed * 1.94384:.0f}")
        p.drawText(QtCore.QRectF(self.width() - 50, c.y() - 7, 48, 14), QtCore.Qt.AlignRight, f"{self.alt / 0.3048:.0f}")
        p.drawText(QtCore.QRectF(0, 2, self.width(), 14), QtCore.Qt.AlignCenter, f"{self.heading % 360:03.0f}")
        p.drawText(QtCore.QRectF(2, self.height() - 16, 60, 14), QtCore.Qt.AlignLeft, "HUD")
        p.end()


class AttentionStrip(QtWidgets.QWidget):
    """Attention-getter strip: small box, wide text box, small box (screenshot).
    Flashes the warning at ~2 Hz while raised, blank otherwise. [a: Behaviour B15]"""

    def __init__(self, parent=None, flash_hz=2.0):
        super().__init__(parent)
        self.setObjectName("attention_strip")
        self.setFixedHeight(30)
        self.text = ""
        self.raised = False
        self.phase_on = False
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(int(500 / flash_hz))
        self.timer.timeout.connect(self._blink)

    def set_warning(self, raised, text="PULL UP"):
        if raised == self.raised:
            return
        self.raised, self.text = raised, text
        if raised:
            self.phase_on = True
            self.timer.start()
        else:
            self.timer.stop()
            self.phase_on = False
        self.update()

    def _blink(self):
        self.phase_on = not self.phase_on
        self.update()

    def shown_text(self):
        return self.text if (self.raised and self.phase_on) else ""

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        h = self.height() - 2
        p.setPen(QtGui.QPen(FRAME, 1))
        left = QtCore.QRectF(1, 1, h, h)
        right = QtCore.QRectF(self.width() - h - 2, 1, h, h)
        mid = QtCore.QRectF(h + 8, 1, self.width() - 2 * h - 18, h)
        on = bool(self.shown_text())
        for rr in (left, mid, right):
            p.fillRect(rr, QtGui.QColor(200, 0, 0) if on else QtGui.QColor(0, 0, 0))
            p.drawRect(rr)
        if on:
            p.setPen(QtGui.QColor(255, 255, 255))
            f = QtGui.QFont("Arial", 12, QtGui.QFont.Bold)
            p.setFont(f)
            p.drawText(mid, QtCore.Qt.AlignCenter, self.text)
        p.end()
