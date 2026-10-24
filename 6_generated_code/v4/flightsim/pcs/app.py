"""PilotControlStation. [a: fsim_TA_PCS.sysml#PilotControlStation] (main_window.py role)

Assembles threadFlightSim (uiManager + ioManager), threadPhysics and threadMapWindow and
realizes the internal bindings of fsim_TA_PCS.sysml:
  threadFlightSim.i_HotasControlOut -> threadPhysics.i_HotasControlIn   (physics.send)
  threadPhysics.i_OwnTelemetryOut   -> threadFlightSim.i_OwnTelemetryIn (physics.latest)
  threadFlightSim.i_OwnStateOut / i_BeaconDataOut / i_TileCachePathOut -> threadMapWindow
  threadMapWindow.i_2dTacticalMapOut -> threadFlightSim.i_2dTileIn      (message window)
Every UI-bearing part is constructed and shown here.

Usage:  python -m flightsim.pcs.app
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt5 import QtCore, QtWidgets

from flightsim.common import ports, cots
from flightsim.pcs.airfields import load_airfields, DEFAULT_ICAO
from flightsim.pcs.physics import ThreadPhysics
from flightsim.pcs.io_manager import BackgroundIOManager
from flightsim.pcs.ui_manager import UserInterfaceManager
from flightsim.pcs.map_window import ThreadMapWindow

AVAILABLE_W, AVAILABLE_H = 1280, 650  # display worst case (R-0): 1280x720 logical minus chrome


class PilotControlStation(QtCore.QObject):
    def __init__(self, data_dir=None, start_network=True, show=True, tile_fetch=None,
                 clock=time.monotonic, launcher=cots.launch, env=None):
        super().__init__()
        self.data_dir = data_dir or os.path.join(os.path.expanduser("~"), "fsim_data")  # all writes redirectable
        self.clock = clock
        self.launcher = launcher
        self.env = env
        self._early = []
        self.airfields = load_airfields(os.path.join(self.data_dir, "airports_local.json"), log=self._early_log)
        # threadPhysics
        self.physics = ThreadPhysics(airfields=self.airfields, icao=DEFAULT_ICAO, send_fdm=start_network)
        # threadFlightSim.ioManager
        self.io = BackgroundIOManager(clock=clock, bind=start_network)
        # threadFlightSim.uiManager
        self.ui = UserInterfaceManager(self.airfields, DEFAULT_ICAO)
        # threadMapWindow (i_TileCachePathOut -> i_TileCachePathIn at construction)
        kw = {"fetch": tile_fetch} if tile_fetch else {}
        self.map_window = ThreadMapWindow(os.path.join(self.data_dir, "tile_cache"), **kw)
        self.map_window.i_2dTacticalMapOut.connect(self._on_map_status)  # -> i_2dTileIn

        self.ui.ssf_requested.connect(self.trigger_ssf)
        self._wire_controls()
        self.ask_save_path = self._default_save_path
        self.ask_open_path = self._default_open_path
        self.otw_process = None
        self._last_sent_controls = None
        self._gear_count = self._airbrake_count = None
        self._last_own_pub = self._last_capture = self._last_map = 0.0
        self.telemetry = self.physics.latest()
        self.ui.init_fields(self.telemetry)
        for m in self._early:
            self.ui.log(m)

        self.io_timer = QtCore.QTimer(self); self.io_timer.timeout.connect(self.io_tick); self.io_timer.start(10)
        self.ui_timer = QtCore.QTimer(self); self.ui_timer.timeout.connect(self.ui_tick); self.ui_timer.start(33)
        self.physics.start()
        if start_network:
            self.io.terrain.start()
        if show:
            self.show_windows()
        self.ui.log("PCS READY. SIM FROZEN: SELECT AIRPORT, THEN RUN SIM.")

    def _early_log(self, m):
        self._early.append(m)

    # ----------------------------------------------------------- windows
    def show_windows(self):
        """Place both windows inside the available area. On a narrow screen the PCS keeps
        its instrument column visible and the map takes the remaining width. [c]"""
        scr = QtWidgets.QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else QtCore.QRect(0, 0, AVAILABLE_W, AVAILABLE_H)
        ui, mp = self.ui, self.map_window
        ui_min, mp_min = ui.minimumSizeHint(), mp.minimumSizeHint()
        ui_w = min(ui.sizeHint().width(), avail.width())
        if ui_w + mp.sizeHint().width() > avail.width():
            ui_w = max(ui_min.width(), min(ui_w, avail.width() - mp_min.width()))
        ui_h = min(ui.sizeHint().height(), avail.height())
        ui.resize(ui_w, ui_h)
        ui.move(avail.left(), avail.top())
        mp_w = max(mp_min.width(), min(mp.sizeHint().width(), avail.width() - ui_w))
        mp_h = min(mp.sizeHint().height(), avail.height())
        mp.resize(mp_w, mp_h)
        mp.move(avail.left() + avail.width() - mp_w, avail.top() + avail.height() - mp_h)
        ui.show()
        mp.show()

    # ---------------------------------------------------------- controls
    def _wire_controls(self):
        ui = self.ui
        ui.btn_engine.clicked.connect(lambda: self.physics.send("engine", self.telemetry["engine_state"] == "OFF"))
        ui.btn_prkbrk.clicked.connect(lambda: self.physics.send("park_brake", not self.telemetry["park_brake"]))
        ui.btn_airbrake.clicked.connect(lambda: self.physics.send("air_brake", not self.telemetry["air_brake"]))
        ui.btn_gear.clicked.connect(lambda: self.physics.send("gear", not self.telemetry["gear_down"]))
        ui.btn_ap.toggled.connect(lambda on: self.physics.send("autopilot", {"ap": on}))
        ui.btn_ah.toggled.connect(lambda on: self.physics.send("autopilot", {"ah": on}))
        ui.btn_at.toggled.connect(lambda on: self.physics.send("autopilot", {"at": on}))
        ui.fields["hdg"].valueChanged.connect(lambda v: self.physics.send("autopilot", {"hdg": v}))
        ui.fields["alt"].valueChanged.connect(lambda v: self.physics.send("autopilot", {"alt": v}))
        ui.fields["spd"].valueChanged.connect(lambda v: self.physics.send("autopilot", {"spd": v}))
        ui.cmb_airport.activated.connect(self._on_airport)

    def _on_airport(self, idx):
        icao = self.ui.cmb_airport.itemData(idx)
        if self.telemetry["running"]:  # [a: L2SYS-REQ-01.04b]
            self.ui.log("AIRPORT CHANGE REFUSED: FLIGHT-FREEZE REQUIRED")
            return
        self.physics.send("select_airport", icao)

    def io_tick(self):
        return self._guarded(self._io_tick)

    def ui_tick(self):
        return self._guarded(self._ui_tick)

    def _io_tick(self):
        """bridgeUdpData + controlManualFlight routing with physical precedence [a: Behaviour B8]."""
        hotas, beacons_changed = self.io.poll()
        vh = self.ui.vhotas
        physical = self.io.physical_active()
        vh.set_locked(physical)
        if physical:
            h = self.io.physical
            # wheel brake: HOTAS button OR the on-screen PEDAL BRAKE (always usable) [c] field defect D-4
            cmd = dict(roll=h.roll, pitch=h.pitch, yaw=h.yaw, throttle=h.throttle,
                       wheel_brake=bool(h.wheel_brake or vh.btn_brake.isDown()))
            vh.mirror(h.roll, h.pitch, h.yaw, h.throttle)
            if self._gear_count is not None and h.gear_toggles != self._gear_count:
                self.physics.send("gear", not self.telemetry["gear_down"])       # [a: Behaviour B5]
            if self._airbrake_count is not None and h.airbrake_toggles != self._airbrake_count:
                self.physics.send("air_brake", not self.telemetry["air_brake"])  # [a: Behaviour B5]
            self._gear_count, self._airbrake_count = h.gear_toggles, h.airbrake_toggles
            self.ui.lbl_hotas.setText("HOTAS: PHYSICAL")
        else:
            cmd = vh.commands()
            self.ui.lbl_hotas.setText("HOTAS: VIRTUAL")
        if cmd != self._last_sent_controls:
            self.physics.send("controls", cmd)
            self._last_sent_controls = cmd
        if beacons_changed:
            self.map_window.i_BeaconDataIn(self.io.beacons)  # i_BeaconDataOut
            self.ui.lbl_threats.setText(f"THREATS: {sum(b.detected for b in self.io.beacons.values())}/{len(self.io.beacons)}")
        elev = self.io.take_terrain()
        if elev is not None:
            self.physics.send("terrain", elev)  # [a: Behaviour B7]

    def _ui_tick(self):
        t = self.physics.latest()
        if t is None:
            return
        self.telemetry = t
        now = self.clock()
        self.ui.update_cockpit_displays(t)
        self.ui.update_attention_getters(t["warning_state"])
        self.ui.set_running(t["running"])
        idx = self.ui.cmb_airport.findData(t["airport"])
        if idx != self.ui.cmb_airport.currentIndex():
            self.ui.cmb_airport.setCurrentIndex(idx)
            self.ui.init_fields(t)
        while not self.physics.messages.empty():
            self.ui.log(self.physics.messages.get_nowait())
        if now - self._last_own_pub >= 1.0 / ports.OWNSHIP_PUBLISH_HZ:
            self._last_own_pub = now
            self.io.publish_ownship(t)
        if now - self._last_map >= 0.1:  # i_OwnStateOut at 10 Hz [c]
            self._last_map = now
            self.map_window.i_OwnStateIn(t)
        if self.io.capture.active and now - self._last_capture >= 0.1:  # 10 Hz [c]
            self._last_capture = now
            self.io.capture.write(t)

    def _on_map_status(self, text):
        if text.startswith("MAP"):
            self.ui.log(text)

    # --------------------------------------------------------------- SSF
    def _guarded(self, fn, *a):
        """A slot never raises into Qt (PyQt5 aborts on unhandled slot exceptions)."""
        try:
            return fn(*a)
        except Exception as e:  # report, keep running
            import traceback
            traceback.print_exc()
            try:
                self.ui.log(f"INTERNAL ERROR: {e!r}")
            except Exception:
                pass

    def trigger_ssf(self, key):
        return self._guarded(self._trigger_ssf, key)

    def _trigger_ssf(self, key):
        """manageSSF: the one gate for buttons and menu. [a: L2SYS-REQ-01.10b] [a: Behaviour B12]"""
        running = self.telemetry["running"]
        if key in ("reset", "set_position", "save", "load") and running:
            self.ui.log(f"{key.upper()} REFUSED: FLIGHT-FREEZE REQUIRED")
            return
        if key == "run":
            self.physics.send("run"); self.ui.log("RUN SIM")
        elif key == "pause":
            self.physics.send("pause"); self.ui.log("PAUSE SIM (FLIGHT-FREEZE)")
        elif key == "reset":
            self.physics.send("reset")
            for b in (self.ui.btn_ap, self.ui.btn_ah, self.ui.btn_at):
                b.blockSignals(True); b.setChecked(False); b.blockSignals(False)
        elif key == "launch":
            self.otw_process = self.launcher(self.telemetry["airport"], self.ui.log, env=self.env)
        elif key == "set_position":
            f = self.ui.fields
            self.physics.send("set_position", dict(lat_deg=f["lat"].value(), lon_deg=f["lon"].value(),
                                                   alt_m=f["alt"].value(), speed_mps=f["spd"].value(),
                                                   heading_deg=f["hdg"].value()))
        elif key == "save":
            self.save_snapshot(self.ask_save_path())
        elif key == "load":
            self.load_snapshot(self.ask_open_path())
        elif key == "fuel_freeze":
            on = self.ui.ssf_buttons["fuel_freeze"].isChecked()
            self.physics.send("fuel_freeze", on); self.ui.log(f"FUEL FREEZE {'ON' if on else 'OFF'}")
        elif key == "capture":
            btn = self.ui.ssf_buttons["capture"]
            if self.io.capture.active:
                self.io.capture.stop(); self.ui.log("TELEMETRY CAPTURE STOPPED")
                btn.setChecked(False)
            else:
                path = os.path.join(self.data_dir, "telemetry",
                                    datetime.datetime.now().strftime("telemetry_%Y%m%d_%H%M%S.csv"))
                self.io.capture.start(path); self.ui.log(f"TELEMETRY CAPTURE: {path}")
                btn.setChecked(True)

    def _default_save_path(self):
        d = os.path.join(self.data_dir, "snapshots")
        os.makedirs(d, exist_ok=True)
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self.ui, "Save Snapshot", d, "Snapshot (*.json)")
        return p

    def _default_open_path(self):
        d = os.path.join(self.data_dir, "snapshots")
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self.ui, "Load Snapshot", d, "Snapshot (*.json)")
        return p

    def snapshot_state(self, timeout=2.0):
        """Full simulation state: physics + beacon list + map view. [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#ssfSaveSnapshot]"""
        import queue as _q
        reply = _q.Queue()
        self.physics.send("save_snapshot", reply)
        if self.physics._thread is None:  # physics loop not running (stepped externally): service now
            self.physics.step()
        try:
            phys = reply.get(timeout=timeout)
        except _q.Empty:
            self.ui.log("SNAPSHOT FAILED: PHYSICS NOT RESPONDING")
            return None
        if phys is None:
            return None
        return dict(physics=phys, map_zoom=self.map_window.mapGuiWorker.zoom,
                    beacons={bid: b.__dict__.copy() for bid, b in self.io.beacons.items()})

    def save_snapshot(self, path):
        if not path:
            return None
        snap = self.snapshot_state()
        if snap is None:
            return None
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=1)
        self.ui.log(f"SNAPSHOT SAVED: {path}")
        return snap

    def load_snapshot(self, path):
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            self.physics.send("load_snapshot", snap["physics"])
            self.map_window.mapGuiWorker.zoom = int(snap.get("map_zoom", self.map_window.mapGuiWorker.zoom))
        except Exception as e:
            self.ui.log(f"SNAPSHOT LOAD FAILED: {e}")
            return None
        return snap

    # ----------------------------------------------------------- shutdown
    def shutdown(self):
        self.io_timer.stop()
        self.ui_timer.stop()
        self.physics.stop()
        self.io.close()
        self.map_window.shutdown()
        self.ui.attention.timer.stop()
        self.ui.close()


def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)  # scaling-aware
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    try:
        pcs = PilotControlStation()
    except OSError as e:
        QtWidgets.QMessageBox.critical(None, "PCS", f"Cannot bind federation ports: {e}\nClose running instances.")
        return 1
    from flightsim.common import observe
    observe.install(app, lambda: [pcs.ui, pcs.map_window], "pcs")
    app.aboutToQuit.connect(pcs.shutdown)
    pcs.ui.closed.connect(app.quit)  # closing the PCS window ends the PCS process
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
