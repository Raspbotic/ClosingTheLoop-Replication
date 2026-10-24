"""Common layer: the port map (D4), the wire format (D1/D19), the beacon
constants (D2) and FlightGear resolution (D16).

Plan rows: C-D1-1 .. C-D1-8, C-D2-2, C-D4-1, C-D16-1 .. C-D16-7.
Additions register group A: test_seq_is_monotonic_across_encodes,
test_decode_rejects_an_unknown_message_kind.

Every reference value below is transcribed from the Decision Annex into this
file; nothing is read back out of the implementation. [a: R-2]
"""

from __future__ import annotations

import json

import pytest

from flightsim.common import config, messages, ports
from flightsim.common.messages import (
    HotasControls,
    OwnshipState,
    RadarBeacon,
    WireFormatError,
)

# ---------------------------------------------------------------------------
# Reference values, transcribed from the Decision Annex.
# ---------------------------------------------------------------------------

# [a: D4] "HOTAS->PCS 5005; PCS->orchestrator 5006; orchestrator->SEM 5007;
# SEM->orchestrator 6007; orchestrator->PCS 6006; PCS->FlightGear native-fdm
# 5550 ...; FlightGear httpd 8080 ...; OSM tiles at
# https://tile.openstreetmap.org"
D4_PORTS = {
    "HOTAS -> PCS": 5005,
    "PCS -> orchestrator": 5006,
    "orchestrator -> SEM": 5007,
    "SEM -> orchestrator": 6007,
    "orchestrator -> PCS": 6006,
    "PCS -> FlightGear native-fdm": 5550,
    "FlightGear httpd": 8080,
}
D4_TILE_ORIGIN = "https://tile.openstreetmap.org"
D4_GROUND_ELEV_PATH = "/json/position/ground-elev-m"

# [a: D19] the 27 field names, in the order D19 gives them.
D19_FIELDS = (
    "seq", "sim_time_s", "lat_deg", "lon_deg", "alt_m", "agl_m",
    "heading_deg", "pitch_deg", "roll_deg", "pitch_rate_dps",
    "speed_mps", "vspeed_mps", "rpm_pct", "fuel_lbs",
    "engine_running", "parking_brake", "air_brake", "gear_down", "on_ground",
    "aileron_cmd", "elevator_cmd", "rudder_cmd", "throttle_cmd",
    "trim_pitch", "trim_roll", "trim_yaw", "model",
)

# [a: D2] "Background refresh every 2.0 s. The receiver discards beacons stale
# for more than 5.0 s (stale >= 2 x refresh)."
D2_REFRESH_S = 2.0
D2_STALE_S = 5.0


def a_state(**over) -> OwnshipState:
    base = dict(
        seq=1, sim_time_s=1.5, lat_deg=45.4451, lon_deg=9.2767, alt_m=107.6,
        agl_m=0.0, heading_deg=176.0, pitch_deg=0.5, roll_deg=-1.25,
        pitch_rate_dps=0.1, speed_mps=0.0, vspeed_mps=0.0, rpm_pct=0.0,
        fuel_lbs=6000.0, engine_running=False, parking_brake=True,
        air_brake=False, gear_down=True, on_ground=True, aileron_cmd=0.0,
        elevator_cmd=0.0, rudder_cmd=0.0, throttle_cmd=0.0, trim_pitch=0.0,
        trim_roll=0.0, trim_yaw=0.0, model="f16",
    )
    base.update(over)
    return OwnshipState(**base)


def a_beacon(**over) -> RadarBeacon:
    base = dict(
        beacon_id="B1", classification="FOE", lat=45.5, lon=9.3, alt=0.0,
        max_range_m=39000.0, detected=True, slant_range_m=12000.0, active=True,
    )
    base.update(over)
    return RadarBeacon(**base)


# ===========================================================================
# C-D4-1 -- every port constant matches D4
# ===========================================================================
def test_every_port_constant_matches_d4():
    """C-D4-1. Port constants against D4. [a: D4] [a: L2SYS-REQ-04.01]"""
    assert ports.HOTAS_TO_PCS == D4_PORTS["HOTAS -> PCS"]
    assert ports.PCS_TO_ORCH == D4_PORTS["PCS -> orchestrator"]
    assert ports.ORCH_TO_SEM == D4_PORTS["orchestrator -> SEM"]
    assert ports.SEM_TO_ORCH == D4_PORTS["SEM -> orchestrator"]
    assert ports.ORCH_TO_PCS == D4_PORTS["orchestrator -> PCS"]
    assert ports.PCS_TO_FLIGHTGEAR_FDM == D4_PORTS["PCS -> FlightGear native-fdm"]
    assert ports.FLIGHTGEAR_HTTPD == D4_PORTS["FlightGear httpd"]


def test_tile_origin_and_ground_elev_path_match_d4():
    """C-D4-1. The two non-numeric endpoints of D4. [a: D4]"""
    assert ports.OSM_TILE_ORIGIN == D4_TILE_ORIGIN
    assert ports.OSM_TILE_HOST == "tile.openstreetmap.org"
    assert ports.FLIGHTGEAR_GROUND_ELEV_PATH == D4_GROUND_ELEV_PATH
    assert ports.ground_elev_url("127.0.0.1", 8080) == (
        "http://127.0.0.1:8080/json/position/ground-elev-m"
    )
    assert ports.tile_url(12, 2145, 1436) == (
        "https://tile.openstreetmap.org/12/2145/1436.png"
    )


def test_no_two_udp_ports_collide():
    """C-D4-1. Six distinct UDP ports; a duplicate would silently cross links."""
    assert len(set(ports.ALL_UDP_PORTS)) == len(ports.ALL_UDP_PORTS) == 6


# ===========================================================================
# C-D1-1..3 -- round trips
# ===========================================================================
def test_round_trip_hotas_controls():
    """C-D1-1. HotasControls survives encode/decode unchanged. [a: D1]"""
    original = HotasControls(
        seq=7, roll=-0.25, pitch=0.5, yaw=0.125, throttle=0.75,
        switches={"2": True, "4": False, "6": True},
    )
    assert messages.decode(messages.encode(original)) == original


def test_round_trip_ownship_state():
    """C-D1-2. All 27 D19 fields survive encode/decode. [a: D1] [a: D19]"""
    original = a_state()
    restored = messages.decode(messages.encode(original))
    assert restored == original
    for name in D19_FIELDS:
        assert getattr(restored, name) == getattr(original, name), name


def test_round_trip_radar_beacon():
    """C-D1-3. RadarBeacon survives encode/decode unchanged. [a: D1]"""
    original = a_beacon()
    assert messages.decode(messages.encode(original)) == original


# ===========================================================================
# C-D1-7 -- D19 field names and order
# ===========================================================================
def test_ownship_state_field_names_and_order_match_d19():
    """C-D1-7. Field tuple equals the D19 list, in order. [a: D19]"""
    assert messages.OWNSHIP_FIELDS == D19_FIELDS
    assert len(D19_FIELDS) == 27
    payload = json.loads(messages.encode(a_state()).decode("utf-8"))
    payload.pop("kind")
    assert set(payload) == set(D19_FIELDS)


# ===========================================================================
# C-D1-4 / C-D1-5 -- ranges on construction AND on decode
# ===========================================================================
@pytest.mark.parametrize(
    "field,bad",
    [("roll", 1.0001), ("roll", -1.0001), ("pitch", 2.0), ("pitch", -2.0),
     ("yaw", 1.5), ("yaw", -1.5), ("throttle", -0.0001), ("throttle", 1.0001)],
)
def test_ranges_enforced_on_construction(field, bad):
    """C-D1-4. Out-of-range control values are refused at construction. [a: D1]"""
    kwargs = dict(seq=0, roll=0.0, pitch=0.0, yaw=0.0, throttle=0.0)
    kwargs[field] = bad
    with pytest.raises(WireFormatError):
        HotasControls(**kwargs)


@pytest.mark.parametrize(
    "field,bad",
    [("roll", 1.5), ("pitch", -1.5), ("yaw", 9.0), ("throttle", 1.5),
     ("throttle", -0.5)],
)
def test_ranges_enforced_on_decode(field, bad):
    """C-D1-5. A datagram carrying an out-of-range value is refused at decode,
    not clipped. [a: D1] "Ranges are enforced on decode as well as on
    construction."
    """
    payload = {
        "kind": "hotas", "seq": 0, "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
        "throttle": 0.0, "switches": {},
    }
    payload[field] = bad
    data = json.dumps(payload).encode("utf-8")
    with pytest.raises(WireFormatError):
        messages.decode(data)


def test_boundary_values_are_accepted():
    """C-D1-4. The stated ranges are inclusive: exactly +-1 and exactly 0 and 1
    are valid, so the refusal above is a range check and not an off-by-one.
    [a: D1]
    """
    ok = HotasControls(seq=0, roll=-1.0, pitch=1.0, yaw=-1.0, throttle=1.0)
    assert ok.throttle == 1.0
    assert HotasControls(seq=0, roll=0, pitch=0, yaw=0, throttle=0.0).throttle == 0.0


# ===========================================================================
# C-D1-6 -- the application's own error type
# ===========================================================================
@pytest.mark.parametrize(
    "data,why",
    [
        (b"", "empty payload"),
        (b"\xff\xfe\x00", "not UTF-8"),
        (b"not json at all", "not JSON"),
        (b"[1,2,3]", "JSON but not an object"),
        (b'"a string"', "JSON but not an object"),
        (b'{"seq":1}', "no kind"),
        (b'{"kind":"hotas","seq":1}', "missing fields"),
        (b'{"kind":"nonsense"}', "unknown kind"),
    ],
)
def test_malformed_datagram_raises_the_applications_own_error(data, why):
    """C-D1-6. Every malformed shape is refused with WireFormatError. [a: D1]"""
    with pytest.raises(WireFormatError):
        messages.decode(data)


def test_the_error_type_is_the_applications_own_not_a_library_error():
    """C-D1-6. D1 requires "a dedicated error type of the application's own", so
    it must not be a standard-library exception a caller could catch by
    accident. [a: D1]
    """
    assert issubclass(WireFormatError, Exception)
    for library_error in (ValueError, KeyError, TypeError, json.JSONDecodeError,
                          ArithmeticError, LookupError, OSError):
        assert not issubclass(WireFormatError, library_error), library_error


def test_truncated_datagram_is_refused():
    """C-D1-6. A datagram cut in half is refused, not half-decoded. [a: D1]"""
    good = messages.encode(a_state())
    with pytest.raises(WireFormatError):
        messages.decode(good[: len(good) // 2])


def test_wrong_typed_and_extra_fields_are_refused():
    """C-D1-6. A field of the wrong type, and an unexpected field, are both
    malformed. [a: D1]
    """
    payload = json.loads(messages.encode(a_state()).decode("utf-8"))
    bad_type = dict(payload, alt_m="high")
    with pytest.raises(WireFormatError):
        messages.decode(json.dumps(bad_type).encode("utf-8"))
    extra = dict(payload, unexpected_field=1)
    with pytest.raises(WireFormatError):
        messages.decode(json.dumps(extra).encode("utf-8"))


def test_bool_is_not_accepted_where_a_number_belongs():
    """C-D1-6. bool subclasses int in Python; True must not become 1.0. [b: D1]"""
    payload = json.loads(messages.encode(a_state()).decode("utf-8"))
    payload["alt_m"] = True
    with pytest.raises(WireFormatError):
        messages.decode(json.dumps(payload).encode("utf-8"))


def test_try_decode_returns_none_instead_of_raising():
    """A receive loop must not die on one bad datagram. [b: D1]"""
    assert messages.try_decode(b"rubbish") is None
    assert messages.try_decode(messages.encode(a_beacon())) == a_beacon()


# ===========================================================================
# C-D1-8 -- classifications
# ===========================================================================
def test_classification_values_are_friend_foe_unknown():
    """C-D1-8. Only the three D1 classifications are accepted. [a: D1]"""
    assert messages.CLASSIFICATIONS == ("FRIEND", "FOE", "UNKNOWN")
    for c in messages.CLASSIFICATIONS:
        assert a_beacon(classification=c).classification == c
    for bad in ("friend", "HOSTILE", "", "NEUTRAL"):
        with pytest.raises(WireFormatError):
            a_beacon(classification=bad)


# ===========================================================================
# C-D2-2 -- tombstone semantics and the stale relation
# ===========================================================================
def test_refresh_and_stale_constants():
    """C-D2-2. Refresh 2.0 s, stale 5.0 s, and stale >= 2 x refresh. [a: D2]"""
    assert messages.BEACON_REFRESH_S == D2_REFRESH_S
    assert messages.BEACON_STALE_S == D2_STALE_S
    # The relation D2 states parenthetically, asserted rather than assumed.
    assert messages.BEACON_STALE_S >= 2.0 * messages.BEACON_REFRESH_S


def test_is_stale_boundary():
    """C-D2-2. "stale for more than 5.0 s" -- exactly 5.0 s is not yet stale.
    [a: D2]
    """
    assert messages.is_stale(last_seen_s=0.0, now_s=5.0001) is True
    assert messages.is_stale(last_seen_s=0.0, now_s=5.0) is False
    assert messages.is_stale(last_seen_s=0.0, now_s=4.9) is False


def test_tombstone_is_an_active_false_beacon():
    """C-D2-2. "Deletion is a tombstone (active=False)" -- and a tombstone is a
    well-formed message that survives the wire. [a: D2]
    """
    tombstone = a_beacon(active=False, detected=False)
    assert tombstone.active is False
    assert messages.decode(messages.encode(tombstone)) == tombstone


# ===========================================================================
# Additions register, group A
# ===========================================================================
def test_seq_is_monotonic_across_encodes():
    """Additions A. A transmitter's sequence numbers must be usable for loss and
    reorder detection, so the counter never repeats or goes backwards. [b: D1]
    """
    c = messages.SeqCounter()
    got = [c.next() for _ in range(5)]
    assert got == [0, 1, 2, 3, 4]
    assert all(b > a for a, b in zip(got, got[1:]))


def test_decode_rejects_an_unknown_message_kind():
    """Additions A. A future message type must not be silently accepted as one
    of the three D1 shapes. [b: D1]
    """
    payload = json.loads(messages.encode(a_beacon()).decode("utf-8"))
    payload["kind"] = "telemetry_v2"
    with pytest.raises(WireFormatError):
        messages.decode(json.dumps(payload).encode("utf-8"))


# ===========================================================================
# C-D16-1 .. C-D16-7 -- FlightGear resolution
#
# Every row here passes an explicit env mapping, an explicit path_dirs and
# explicit filesystem predicates, so none of them reads the host. [a: D18]
# ===========================================================================
def _fs(files=(), dirs=()):
    """Filesystem predicates over two literal sets. [a: D18]"""
    files = set(files)
    dirs = set(dirs)

    def isfile(p):
        return p in files

    def isdir(p):
        return p in dirs

    def listdir(p):
        out = set()
        for d in dirs | files:
            if d.startswith(p + "\\"):
                out.add(d[len(p) + 1:].split("\\")[0])
        return sorted(out)

    return isfile, isdir, listdir


def test_explicit_executable_wins(clean_env):
    """C-D16-1. FSIM_FG_EXECUTABLE has the highest precedence and is used
    verbatim. [a: D16]
    """
    isfile, isdir, listdir = _fs(
        files={r"D:\custom\fgfs.exe", r"C:\Program Files\FlightGear 1.0\bin\fgfs.exe"},
        dirs={r"C:\Program Files", r"C:\Program Files\FlightGear 1.0"},
    )
    cfg = config.resolve_flightgear(
        env={
            "FSIM_FG_EXECUTABLE": r"D:\custom\fgfs.exe",
            "FSIM_FG_HOME": r"C:\Program Files\FlightGear 1.0",
        },
        path_dirs=[],
        isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg.executable == r"D:\custom\fgfs.exe"
    assert cfg.resolved is True
    assert cfg.source == config.SOURCE_EXECUTABLE_VAR


def test_explicit_executable_is_used_verbatim_even_when_absent(clean_env):
    """C-D16-1. "used verbatim" -- the value is not rewritten when the file is
    missing; only `resolved` records that it is not there. [a: D16]
    """
    isfile, isdir, listdir = _fs()
    cfg = config.resolve_flightgear(
        env={"FSIM_FG_EXECUTABLE": r"D:\gone\fgfs.exe"},
        path_dirs=[], isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg.executable == r"D:\gone\fgfs.exe"
    assert cfg.resolved is False


def test_install_root_derives_bin_fgfs(clean_env):
    """C-D16-2. FSIM_FG_HOME derives <root>\\bin\\fgfs.exe. [a: D16]"""
    root = r"C:\FG"
    isfile, isdir, listdir = _fs(files={root + r"\bin\fgfs.exe"}, dirs={root})
    cfg = config.resolve_flightgear(
        env={"FSIM_FG_HOME": root}, path_dirs=[],
        isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg.executable == r"C:\FG\bin\fgfs.exe"
    assert cfg.source == config.SOURCE_HOME_VAR
    assert cfg.resolved is True


def test_path_is_searched_third(clean_env):
    """C-D16-3. With no variable set, PATH is searched -- and the PATH searched
    is the one supplied as a parameter, never the host's. [a: D16] [a: D18]
    """
    isfile, isdir, listdir = _fs(files={r"C:\tools\fgfs.exe"}, dirs={r"C:\tools"})
    cfg = config.resolve_flightgear(
        env={}, path_dirs=[r"C:\nothing", r"C:\tools"],
        isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg.executable == r"C:\tools\fgfs.exe"
    assert cfg.source == config.SOURCE_PATH


def test_program_files_scan_orders_newest_first(clean_env):
    """C-D16-4. The scan is ordered on the version in the directory name, newest
    first. [a: D16]
    """
    pf = r"C:\Program Files"
    names = ["FlightGear 2.4.0", "FlightGear 10.1.0", "FlightGear 2.20.3",
             "FlightGear"]
    dirs = {pf} | {pf + "\\" + n for n in names}
    files = {pf + "\\" + n + r"\bin\fgfs.exe" for n in names}
    isfile, isdir, listdir = _fs(files=files, dirs=dirs)
    cfg = config.resolve_flightgear(
        env={}, path_dirs=[], program_files_root=pf,
        isfile=isfile, isdir=isdir, listdir=listdir,
    )
    # 10.1.0 > 2.20.3 > 2.4.0 numerically, which a lexical sort would get wrong.
    assert cfg.executable == pf + r"\FlightGear 10.1.0\bin\fgfs.exe"
    assert cfg.source == config.SOURCE_PROGRAM_FILES
    # and the ordering itself, independent of the filesystem
    ordered = sorted(names, key=config.version_key, reverse=True)
    assert ordered[0] == "FlightGear 10.1.0"
    assert ordered[-1] == "FlightGear"


def test_unresolved_reports_the_bare_name_and_says_so(clean_env):
    """C-D16-5. With nothing resolvable, the bare program name of D6 is returned
    and flagged unresolved, and the hint names the variable to set. [a: D16]
    """
    isfile, isdir, listdir = _fs()
    cfg = config.resolve_flightgear(
        env={}, path_dirs=[], isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg.executable == config.FGFS_PROGRAM_NAME == "fgfs"
    assert cfg.resolved is False
    assert cfg.source == config.SOURCE_UNRESOLVED
    assert config.ENV_EXECUTABLE in cfg.missing_variable_hint
    assert config.ENV_HOME in cfg.missing_variable_hint


def test_exported_variable_beats_the_file(clean_env):
    """C-D16-6. "an exported variable beats the file". [a: D16]"""
    file_text = 'set FSIM_FG_EXECUTABLE=C:\\from_file\\fgfs.exe\n'
    isfile, isdir, listdir = _fs(
        files={r"C:\from_file\fgfs.exe", r"C:\from_env\fgfs.exe"}
    )
    cfg = config.resolve_flightgear(
        env={"FSIM_FG_EXECUTABLE": r"C:\from_env\fgfs.exe"},
        env_file_text=file_text, path_dirs=[],
        isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg.executable == r"C:\from_env\fgfs.exe"

    # and with nothing exported, the file's value is used
    cfg2 = config.resolve_flightgear(
        env={}, env_file_text=file_text, path_dirs=[],
        isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg2.executable == r"C:\from_file\fgfs.exe"


def test_env_file_is_parsed_directly():
    """C-D16-7. set lines, comments, blanks and quotes parse; a malformed line
    is ignored rather than raising. [a: D16]
    """
    text = (
        "@echo off\n"
        "REM a comment\n"
        ":: another comment\n"
        "\n"
        "set FSIM_FG_EXECUTABLE=C:\\FG\\bin\\fgfs.exe\n"
        'set "FSIM_FG_HOME=C:\\FG"\n'
        "SET fsim_fg_aircraft_dir=C:\\Aircraft\n"
        "this line is not an assignment\n"
        "set PATH=C:\\evil\n"
        "set FSIM_FG_ROOT=\n"
    )
    got = config.parse_env_file(text)
    assert got == {
        "FSIM_FG_EXECUTABLE": r"C:\FG\bin\fgfs.exe",
        "FSIM_FG_HOME": r"C:\FG",
        "FSIM_FG_AIRCRAFT_DIR": r"C:\Aircraft",
    }
    # An empty value is not a setting, and PATH can never come from this file.
    assert "FSIM_FG_ROOT" not in got
    assert "PATH" not in got


def test_missing_aircraft_directory_warns_but_does_not_block(clean_env):
    """C-D16-*. "a missing aircraft directory warns". [a: D16] [a: Sec.5.3]"""
    isfile, isdir, listdir = _fs(files={r"C:\FG\bin\fgfs.exe"}, dirs={r"C:\FG"})
    cfg = config.resolve_flightgear(
        env={"FSIM_FG_HOME": r"C:\FG", "FSIM_FG_AIRCRAFT_DIR": r"C:\NoSuch"},
        path_dirs=[], isfile=isfile, isdir=isdir, listdir=listdir,
    )
    assert cfg.resolved is True          # not blocked
    assert cfg.warnings                  # but warned
    assert any("default aircraft" in w for w in cfg.warnings)


# ===========================================================================
# D6 launch command
# ===========================================================================
def test_launch_command_matches_d6():
    """The D6 argv, including that a bracketed argument appears only when its
    variable is set. [a: D6]
    """
    cfg = config.FlightGearConfig(
        executable=r"C:\FG\bin\fgfs.exe", resolved=True,
        source=config.SOURCE_HOME_VAR,
        aircraft_dir=r"C:\Aircraft", fg_root=r"C:\FG\data",
    )
    argv = config.build_fgfs_command(cfg, icao="LIML",
                                     fdm_port=ports.PCS_TO_FLIGHTGEAR_FDM)
    assert argv[0] == r"C:\FG\bin\fgfs.exe"
    assert "--fdm=null" in argv
    assert "--native-fdm=socket,in,60,,5550,udp" in argv
    assert r"--fg-aircraft=C:\Aircraft" in argv
    assert "--aircraft=YF-16" in argv
    assert "--airport=LIML" in argv
    assert "--timeofday=noon" in argv
    assert "--httpd=8080" in argv
    assert "--prop:/input/joysticks/js[0]=0" in argv
    assert "--prop:/input/joysticks/js[1]=0" in argv
    assert r"--fg-root=C:\FG\data" in argv


def test_bracketed_arguments_are_absent_when_unset():
    """[a: D6] "A bracketed argument appears only when its variable is set.\""""
    cfg = config.FlightGearConfig(
        executable="fgfs", resolved=False, source=config.SOURCE_UNRESOLVED,
        aircraft_dir=None, fg_root=None,
    )
    argv = config.build_fgfs_command(cfg, icao="KSFO",
                                     fdm_port=ports.PCS_TO_FLIGHTGEAR_FDM)
    assert not any(a.startswith("--fg-aircraft=") for a in argv)
    assert not any(a.startswith("--fg-root=") for a in argv)
    assert "--aircraft=YF-16" in argv


def test_no_flightgear_version_is_pinned_in_the_command():
    """[a: Sec.5.3] "No FlightGear version number appears in the code or the
    tests." The command carries no version, whatever the install directory is
    called.
    """
    # A deliberately fictional version: Sec.5.3 forbids a FlightGear version
    # number appearing in the code OR the tests, and a real release number here
    # would itself be a violation. The point of the row is that whatever the
    # host's directory is called, none of it reaches the arguments.
    host_directory = "FlightGear 9.9.9"
    cfg = config.FlightGearConfig(
        executable=rf"C:\Program Files\{host_directory}\bin\fgfs.exe",
        resolved=True, source=config.SOURCE_PROGRAM_FILES,
    )
    argv = config.build_fgfs_command(cfg, icao="LIML", fdm_port=5550)
    # the version appears only inside the resolved path the host supplied,
    # never as an argument this project constructs
    constructed = list(argv[1:])
    assert not any("9.9.9" in a for a in constructed)
    assert not any(host_directory in a for a in constructed)
