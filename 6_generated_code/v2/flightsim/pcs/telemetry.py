"""Telemetry CSV capture -- Qt-free. [a: D14] [a: L2SYS-REQ-01.10b]

[a: L2SYS-REQ-01.10b] "TELEMETRY CAPTURE: log telemetry to CSV file."
[a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml] `ssfLogTelemetry`, whose doc lists
the same fields.

[a: D14] gives the header as **28 fields in order**:

    wall_time_iso, sim_time_s, model, lat_deg, lon_deg, alt_m, agl_m,
    v_speed_mps, speed_mps, heading_deg, target_heading_deg, pitch_deg,
    pitch_rate_dps, roll_deg, target_alt_m, target_speed_mps,
    aileron_cmd, elevator_cmd, rudder_cmd, throttle_cmd, gear_cmd,
    trim_pitch, trim_roll, trim_yaw,
    ap_engaged, ah_engaged, at_engaged, warning_state

Note the names are D14's, not D19's: D14 writes `v_speed_mps` where D19 has
`vspeed_mps`, and adds `target_heading_deg`, `target_alt_m`,
`target_speed_mps`, `gear_cmd`, the three engagement flags and
`warning_state`, which come from the autopilot and the attention getter rather
than from the ownship state. The mapping is explicit in `row()` so neither name
set is silently assumed to be the other.

The output directory is a **parameter**, so the suite redirects it into
`tmp_path` and a suite run writes nothing elsewhere. [a: D18]
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
from typing import Callable, TextIO

from flightsim.common.messages import OwnshipState

#: [a: D14] the header, verbatim and in order. 28 fields.
TELEMETRY_FIELDS: tuple[str, ...] = (
    "wall_time_iso",
    "sim_time_s",
    "model",
    "lat_deg",
    "lon_deg",
    "alt_m",
    "agl_m",
    "v_speed_mps",
    "speed_mps",
    "heading_deg",
    "target_heading_deg",
    "pitch_deg",
    "pitch_rate_dps",
    "roll_deg",
    "target_alt_m",
    "target_speed_mps",
    "aileron_cmd",
    "elevator_cmd",
    "rudder_cmd",
    "throttle_cmd",
    "gear_cmd",
    "trim_pitch",
    "trim_roll",
    "trim_yaw",
    "ap_engaged",
    "ah_engaged",
    "at_engaged",
    "warning_state",
)

assert len(TELEMETRY_FIELDS) == 28, "D14 states 28 fields"


def row(
    state: OwnshipState,
    target_alt_m: float = 0.0,
    target_speed_mps: float = 0.0,
    target_heading_deg: float = 0.0,
    ap_engaged: bool = False,
    ah_engaged: bool = False,
    at_engaged: bool = False,
    warning_state: str = "",
    wall_time_iso: str | None = None,
) -> list:
    """One CSV row, in D14's field order. [a: D14]"""
    return [
        wall_time_iso if wall_time_iso is not None else now_iso(),
        state.sim_time_s,
        state.model,
        state.lat_deg,
        state.lon_deg,
        state.alt_m,
        state.agl_m,
        state.vspeed_mps,          # D14 'v_speed_mps' <- D19 'vspeed_mps'
        state.speed_mps,
        state.heading_deg,
        target_heading_deg,
        state.pitch_deg,
        state.pitch_rate_dps,
        state.roll_deg,
        target_alt_m,
        target_speed_mps,
        state.aileron_cmd,
        state.elevator_cmd,
        state.rudder_cmd,
        state.throttle_cmd,
        1 if state.gear_down else 0,   # D14 'gear_cmd' <- D19 'gear_down'
        state.trim_pitch,
        state.trim_roll,
        state.trim_yaw,
        int(bool(ap_engaged)),
        int(bool(ah_engaged)),
        int(bool(at_engaged)),
        warning_state,
    ]


def now_iso() -> str:
    """Wall-clock time, ISO 8601. [a: D14] `wall_time_iso`."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def default_filename(prefix: str = "telemetry") -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}.csv"


class TelemetryCapture:
    """The TELEMETRY CAPTURE toggle. [a: L2SYS-REQ-01.10b]

    `directory` is a parameter with no default, so nothing can be written
    outside a path the caller chose. [a: D18]
    """

    def __init__(
        self,
        directory: str,
        filename: str | None = None,
        clock_iso: Callable[[], str] | None = None,
    ) -> None:
        self.directory = directory
        self.filename = filename or default_filename()
        self._clock_iso = clock_iso or now_iso
        self._fh: TextIO | None = None
        self._writer = None
        self.rows_written = 0

    @property
    def path(self) -> str:
        return os.path.join(self.directory, self.filename)

    @property
    def engaged(self) -> bool:
        return self._fh is not None

    def start(self) -> str:
        """Open the file and write the header. [a: D14]"""
        if self._fh is not None:
            return self.path
        os.makedirs(self.directory, exist_ok=True)
        self._fh = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(TELEMETRY_FIELDS)
        self._fh.flush()
        self.rows_written = 0
        return self.path

    def write(self, state: OwnshipState, **kw) -> None:
        if self._writer is None:
            return
        self._writer.writerow(row(state, wall_time_iso=self._clock_iso(), **kw))
        self.rows_written += 1

    def stop(self) -> str | None:
        """Close the file. Returns the path written, or None if never started."""
        if self._fh is None:
            return None
        path = self.path
        try:
            self._fh.flush()
            self._fh.close()
        finally:
            self._fh = None
            self._writer = None
        return path

    def toggle(self, state_dir: str | None = None) -> bool:
        """Engage or disengage. Returns the new engaged state."""
        if self.engaged:
            self.stop()
            return False
        if state_dir:
            self.directory = state_dir
        self.filename = default_filename()
        self.start()
        return True

    def __enter__(self) -> "TelemetryCapture":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
