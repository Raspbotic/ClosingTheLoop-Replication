"""
flightsim.pcs.virtual_hotas
===========================
On-screen virtual flight controls (stick, throttle, pedals).

TRACEABILITY
------------
[a] L2SYS-REQ-01.08b: "The PCS shall provide on-screen virtual flight
    controls (stick, throttle, pedals) usable when the physical HOTAS is
    absent; physical HOTAS input shall take precedence when present."
    Allocated: PilotControlStation (FCN-01.8). Layout per the customer
    reference screenshots (Throttle slider | FBW stick pad | Pedals
    slider | PEDAL BRAKE button).
[b] Precedence is enforced in IOManager (main_window.py): physical
    datagrams on UDP 5005 within PHYSICAL_TIMEOUT_S win; this widget is
    greyed out while physical input is active.

DEVELOPER CHOICES [c]
---------------------
- Stick and pedals SPRING BACK to neutral on mouse release (fly-by-wire
  centering); throttle holds its position -- matching real HOTAS
  ergonomics.
- PEDAL BRAKE is momentary (held = braking), mapped to the btn6 switch
  channel, i.e. the same wire meaning as the physical brake button.
- Axis ranges identical to the physical path: roll/pitch/yaw in
  [-1, +1], throttle in [0, 1].
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QPainter, QPen, QColor
from PyQt5.QtWidgets import (QGroupBox, QWidget, QHBoxLayout, QVBoxLayout,
                             QSlider, QLabel, QPushButton)

from flightsim.common.messages import HotasControls


class StickPad(QWidget):
    """Square drag pad: dot position = (roll, pitch) in [-1, +1].
    Springs back to center on release [c]."""

    def __init__(self):
        super().__init__()
        self.roll = 0.0
        self.pitch = 0.0
        self.setMinimumSize(140, 140)

    # -- input --
    def _apply_pos(self, pos) -> None:
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 8
        cx, cy = w / 2, h / 2
        dx = max(-1.0, min(1.0, (pos.x() - cx) / r))
        dy = max(-1.0, min(1.0, (pos.y() - cy) / r))
        # [b: bug fix 2026-07-12] stick top-view convention, matching the
        # physical HOTAS sign: drag DOWN = stick back = pitch +1 (nose-up),
        # drag UP = stick forward = pitch -1 (nose-down)
        self.roll, self.pitch = dx, dy
        self.update()

    def mousePressEvent(self, e):
        self._apply_pos(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._apply_pos(e.pos())

    def mouseReleaseEvent(self, e):
        self.center()

    def center(self) -> None:
        self.roll = self.pitch = 0.0
        self.update()

    # programmatic access (tests / automation)
    def set_axes(self, roll: float, pitch: float) -> None:
        self.roll = max(-1.0, min(1.0, roll))
        self.pitch = max(-1.0, min(1.0, pitch))
        self.update()

    # -- render --
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 8
        p.setPen(QPen(QColor("#33404c"), 2))
        p.setBrush(QColor("#0c1014"))
        p.drawEllipse(QPointF(w / 2, h / 2), r, r)
        # dot at current deflection (pitch positive = stick back = dot down
        # would be ambiguous; render pilot-view: back = dot toward pilot,
        # i.e. down)  [c: matches reference screenshot]
        dot = QPointF(w / 2 + self.roll * r, h / 2 + self.pitch * r)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#39d353"))
        p.drawEllipse(dot, 9, 9)
        p.end()


class SpringSlider(QSlider):
    """[b: bug fix 2026-07-13] slider that springs back to neutral on
    ANY mouse release. The previous sliderReleased-signal approach missed
    groove clicks (page-steps), which only emit valueChanged -- so the
    pedals could stick off-center. Overriding mouseReleaseEvent catches
    every release path."""

    def __init__(self, orientation, neutral: int = 0):
        super().__init__(orientation)
        self._neutral = neutral

    def mouseReleaseEvent(self, e):
        if e is not None:
            super().mouseReleaseEvent(e)
        self.setValue(self._neutral)


class ThrottleSlider(QSlider):
    """[b: customer request 2026-07-13] throttle with a % scale painted
    beside the groove: IDLE 0 / 25 / 50 / 75 / MIL 100, plus the AB
    (afterburner) zone marker at the top. [c] JSBSim's F16 turbine
    engages augmentation at throttle-cmd ~= 1.0, so AB sits at the very
    top of the travel."""

    _MARKS = [(100, "MIL 100"), (75, "75"), (50, "50"),
              (25, "25"), (0, "IDLE 0")]

    def __init__(self):
        super().__init__(Qt.Vertical)
        self.setRange(0, 100)
        self.setValue(0)
        self.setMinimumWidth(86)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setPen(QColor("#9fb2c0"))
        f = p.font(); f.setPointSize(7); p.setFont(f)
        h, top, bot = self.height(), 6, 6
        span = h - top - bot
        for pct, label in self._MARKS:
            y = int(top + span * (100 - pct) / 100)
            p.drawLine(30, y, 38, y)
            p.drawText(42, y + 3, label)
        # AB zone marker (top of travel) [c]
        p.setPen(QColor("#ff5050"))
        p.drawText(42, top + 1, "AB")
        p.drawLine(30, top, 38, top)
        p.end()


class VirtualHotas(QGroupBox):
    """Assembly: Throttle | Stick | Pedals + PEDAL BRAKE [a: 01.08b]."""

    TITLE_ACTIVE = "Virtual HOTAS"
    TITLE_LOCKED = "Virtual HOTAS (physical HOTAS active)"

    def __init__(self):
        super().__init__(self.TITLE_ACTIVE)
        row = QHBoxLayout(self)

        # Throttle: vertical, holds position [c]
        tcol = QVBoxLayout()
        tcol.addWidget(QLabel("Throttle"))
        self.sl_throttle = ThrottleSlider()
        tcol.addWidget(self.sl_throttle, 1)
        row.addLayout(tcol)

        # Stick pad
        scol = QVBoxLayout()
        scol.addWidget(QLabel("FBW Stick"))
        self.stick = StickPad()
        scol.addWidget(self.stick, 1)
        # Pedals: horizontal, springs back on release [c]
        scol.addWidget(QLabel("Pedals"))
        self.sl_pedals = SpringSlider(Qt.Horizontal, neutral=0)
        self.sl_pedals.setRange(-100, 100)
        self.sl_pedals.setValue(0)
        scol.addWidget(self.sl_pedals)
        # PEDAL BRAKE: momentary [c], wire-equivalent to btn6
        self.btn_brake = QPushButton("PEDAL BRAKE")
        self._braking = False
        self.btn_brake.pressed.connect(lambda: self._set_brake(True))
        self.btn_brake.released.connect(lambda: self._set_brake(False))
        scol.addWidget(self.btn_brake)
        row.addLayout(scol, 1)

    def _set_brake(self, on: bool) -> None:
        self._braking = on

    # ------------------------------------------------------------------
    # Control source interface consumed by IOManager [b]
    # ------------------------------------------------------------------
    def read(self) -> HotasControls:
        return HotasControls(
            roll=self.stick.roll,
            pitch=self.stick.pitch,
            yaw=self.sl_pedals.value() / 100.0,
            throttle=self.sl_throttle.value() / 100.0,
            switches={"btn6": self._braking},
        )

    def set_physical_active(self, active: bool) -> None:
        """Grey out while the physical HOTAS has precedence [a: 01.08b].
        [c: design choice 2026-07-13] on the physical->virtual transition
        (disconnect/timeout) the mirrored stick and pedals CENTER (they
        are spring-loaded; nobody is holding them) while the throttle
        RETAINS its last physical position (throttles hold) -- bumpless
        thrust through a HOTAS loss."""
        was_active = getattr(self, "_phys_active", False)
        self._phys_active = active
        if was_active and not active:
            self.stick.center()
            self.sl_pedals.setValue(0)
        self.setEnabled(not active)
        self.setTitle(self.TITLE_LOCKED if active else self.TITLE_ACTIVE)

    def mirror(self, c: HotasControls) -> None:
        """[b: customer request 2026-07-13] while the physical HOTAS is
        active, the (greyed) widget FOLLOWS it: stick dot, throttle and
        pedals display the live physical commands. Safe because the
        sliders have no connected change-slots (IOManager polls read()
        only when the widget is the active source)."""
        self.stick.set_axes(c.roll, c.pitch)
        self.sl_throttle.setValue(int(round(c.throttle * 100)))
        self.sl_pedals.setValue(int(round(c.yaw * 100)))