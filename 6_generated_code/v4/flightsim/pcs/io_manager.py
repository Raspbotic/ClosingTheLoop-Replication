"""BackgroundIOManager. [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml#BackgroundIOManager]

bridgeUdpData: e_hotasControlIn 5005 (rx), e_radarDetectionsIn 6006 (rx), e_ownshipStateOut
5006 (tx). terrainElevationManager: HTTP GET 8080 at 2 Hz [a: Behaviour B7].
telemetryCaptureManager: CSV per ssfLogTelemetry [a: L2SYS-REQ-01.10b].
Physical precedence window [a: Behaviour B8] [a: L2SYS-REQ-01.08b].
Beacon reception [a: L2SYS-REQ-02.03] with B2 staleness and tombstones.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import socket
import threading
import time
import urllib.request

from flightsim.common import ports
from flightsim.common import messages as msg

PRECEDENCE_WINDOW_S = 1.0  # [a: Behaviour B8] model silent

# [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#ssfLogTelemetry] field list, verbatim order
TELEMETRY_FIELDS = ["wall_time_iso", "sim_time_s", "model", "lat_deg", "lon_deg", "alt_m", "agl_m",
                    "v_speed_mps", "speed_mps", "heading_deg", "target_heading_deg", "pitch_deg",
                    "pitch_rate_dps", "roll_deg", "target_alt_m", "target_speed_mps",
                    "aileron_cmd", "elevator_cmd", "rudder_cmd", "throttle_cmd", "gear_cmd",
                    "trim_pitch", "trim_roll", "trim_yaw", "ap_engaged", "ah_engaged", "at_engaged",
                    "warning_state"]


def parse_ground_elev(body: bytes):
    """FlightGear /json property node -> metres. The value may arrive as a string. [a: Behaviour B7]"""
    d = json.loads(body.decode("utf-8"))
    v = d.get("value") if isinstance(d, dict) else d
    return float(v)


def _http_get(url, timeout=0.4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


class TerrainPoller:
    """terrainElevationManager: background thread, stoppable, tolerant of an absent endpoint."""

    def __init__(self, on_elevation, get=_http_get, rate_hz=ports.TERRAIN_POLL_HZ,
                 url=f"http://{ports.LOOPBACK}:{ports.FG_HTTPD_PORT}{ports.FG_GROUND_ELEV_PATH}"):
        self.on_elevation, self.get, self.url = on_elevation, get, url
        self.period = 1.0 / rate_hz
        self._stop = threading.Event()
        self._thread = None
        self.polls = 0
        self.ok = 0

    def poll_once(self):
        self.polls += 1
        try:
            elev = parse_ground_elev(self.get(self.url))
        except Exception:
            return None  # no visual system running: tolerated [a: Behaviour B7]
        self.ok += 1
        self.on_elevation(elev)
        return elev

    def _run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.period)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="TerrainPoller")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2.0)
            self._thread = None


class TelemetryCapture:
    """telemetryCaptureManager."""

    def __init__(self):
        self.file = None
        self.writer = None
        self.path = None
        self.rows = 0

    def start(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.file = open(path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.writer.writerow(TELEMETRY_FIELDS)
        self.path = path
        self.rows = 0

    def write(self, t):
        if not self.writer:
            return
        row = []
        for k in TELEMETRY_FIELDS:
            if k == "wall_time_iso":
                row.append(datetime.datetime.now().isoformat(timespec="milliseconds"))
            else:
                v = t.get(k, "")
                row.append(f"{v:.6f}" if isinstance(v, float) else v)
        self.writer.writerow(row)
        self.rows += 1

    def stop(self):
        if self.file:
            self.file.close()
        self.file = self.writer = None

    @property
    def active(self):
        return self.writer is not None


class BackgroundIOManager:
    def __init__(self, clock=time.monotonic, host=ports.LOOPBACK, bind=True, http_get=_http_get):
        self.clock = clock
        self.host = host
        self.rx_hotas = self.rx_radar = None
        if bind:
            self.rx_hotas = self._bind(ports.HOTAS_CONTROL_PORT)
            self.rx_radar = self._bind(ports.PCS_RADAR_IN_PORT)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.own_addr = (host, ports.PCS_OWNSHIP_OUT_PORT)
        self.physical = None          # last HotasControls
        self.physical_t = None
        self.beacons = {}             # beacon_id -> RadarBeacon
        self.beacon_seen = {}
        self.rejected = 0
        self.own_seq = 0
        self.capture = TelemetryCapture()
        self.terrain = TerrainPoller(self._on_elev, get=http_get)
        self.terrain_value = None
        self._elev_lock = threading.Lock()

    def _bind(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((self.host, port))
        s.setblocking(False)
        return s

    def _on_elev(self, e):
        with self._elev_lock:
            self.terrain_value = e

    def take_terrain(self):
        with self._elev_lock:
            v, self.terrain_value = self.terrain_value, None
        return v

    def _drain(self, sock):
        out = []
        if sock is None:
            return out
        while True:
            try:
                data = sock.recv(65535)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            try:
                out.append(msg.decode(data))
            except msg.MessageError:
                self.rejected += 1  # a bad datagram never stops the loop [a: Behaviour B1]
        return out

    def poll(self):
        """bridgeUdpData inbound. Returns (hotas_messages, beacons_changed)."""
        now = self.clock()
        hotas = [m for m in self._drain(self.rx_hotas) if isinstance(m, msg.HotasControls)]
        if hotas:
            self.physical = hotas[-1]
            self.physical_t = now
        changed = False
        for b in self._drain(self.rx_radar):
            if not isinstance(b, msg.RadarBeacon):
                continue
            changed = True
            if b.active:
                self.beacons[b.beacon_id] = b
                self.beacon_seen[b.beacon_id] = now
            else:  # tombstone [a: Behaviour B2]
                self.beacons.pop(b.beacon_id, None)
                self.beacon_seen.pop(b.beacon_id, None)
        for bid, t in list(self.beacon_seen.items()):
            if now - t > msg.BEACON_STALE_S:  # [a: Behaviour B2]
                self.beacons.pop(bid, None)
                self.beacon_seen.pop(bid, None)
                changed = True
        return hotas, changed

    def physical_active(self):
        """[a: Behaviour B8] a fresh physical datagram claims control for the window."""
        return self.physical_t is not None and (self.clock() - self.physical_t) < PRECEDENCE_WINDOW_S

    def publish_ownship(self, t):
        """bridgeUdpData outbound: OwnshipState on 5006."""
        self.own_seq += 1
        o = msg.OwnshipState(seq=self.own_seq, sim_time_s=max(0.0, t["sim_time_s"]), lat_deg=t["lat_deg"],
                             lon_deg=t["lon_deg"], alt_m=t["alt_m"], agl_m=max(-100.0, t["agl_m"]),
                             v_speed_mps=t["v_speed_mps"], speed_mps=max(0.0, t["speed_mps"]),
                             heading_deg=t["heading_deg"] % 360.0, pitch_deg=t["pitch_deg"],
                             roll_deg=((t["roll_deg"] + 180.0) % 360.0) - 180.0)
        try:
            self.tx.sendto(msg.encode(o), self.own_addr)
        except (OSError, msg.MessageError):
            pass

    def close(self):
        self.terrain.stop()
        self.capture.stop()
        for s in (self.rx_hotas, self.rx_radar, self.tx):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
