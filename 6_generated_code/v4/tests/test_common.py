"""Conformance rows C-PORT, C-B1, C-B2, C-B4. [a: Behaviour B1] [a: Behaviour B2] [a: Behaviour B4]"""
import json
import pytest

from flightsim.common import ports, messages as m, cots


def test_port_constants():
    """C-PORT: literals transcribed from the TA elements (fsim_TA_*.sysml, fsim_TA_IF_Requirements.sysml)."""
    assert ports.HOTAS_CONTROL_PORT == 5005
    assert ports.PCS_OWNSHIP_OUT_PORT == 5006
    assert ports.SEM_OWNSHIP_IN_PORT == 5007
    assert ports.SEM_RADAR_OUT_PORT == 6007
    assert ports.PCS_RADAR_IN_PORT == 6006
    assert ports.NATIVE_FDM_PORT == 5550
    assert ports.FG_HTTPD_PORT == 8080
    assert ports.OSM_TILE_HOST == "tile.openstreetmap.org"
    assert ports.PHYSICS_RATE_HZ == 120.0


def _samples():
    return [
        m.HotasControls(seq=1, roll=0.5, pitch=-0.25, yaw=0.0, throttle=0.7, gear_toggles=2, airbrake_toggles=1, wheel_brake=True),
        m.OwnshipState(seq=3, sim_time_s=1.5, lat_deg=48.3, lon_deg=11.7, alt_m=450.0, agl_m=1.0, v_speed_mps=0.0,
                       speed_mps=0.0, heading_deg=81.0, pitch_deg=0.5, roll_deg=0.0),
        m.RadarBeacon(beacon_id=7, classification="FOE", lat_deg=48.4, lon_deg=11.8, alt_m=500.0, max_range_m=39000.0),
    ]


@pytest.mark.parametrize("msg", _samples(), ids=["hotas", "ownship", "beacon"])
def test_roundtrip(msg):
    assert m.decode(m.encode(msg)) == msg


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"not json", b"[1,2,3]", b'{"type":"x"}',
                                 b'{"type":"hotas","seq":1}',
                                 b'{"type":"beacon","beacon_id":1,"classification":"FOE","lat_deg":0,"lon_deg":0,"alt_m":0,"max_range_m":1,"detected":false,"slant_range_m":-1,"active":true,"zz":1}'])
def test_malformed_rejected(raw):
    with pytest.raises(m.MessageError):
        m.decode(raw)


def test_range_rejected():
    d = json.loads(m.encode(_samples()[0]))
    d["roll"] = 1.5
    with pytest.raises(m.MessageError):
        m.decode(json.dumps(d).encode())
    d = json.loads(m.encode(_samples()[2]))
    d["classification"] = "ALLY"
    with pytest.raises(m.MessageError):
        m.decode(json.dumps(d).encode())
    d["classification"] = "FOE"
    d["active"] = "no"
    with pytest.raises(m.MessageError):
        m.decode(json.dumps(d).encode())


def test_tombstone():
    b = _samples()[2]
    b.active = False
    assert m.decode(m.encode(b)).active is False


def test_staleness_two_periods():
    assert m.BEACON_REFRESH_S == 2.0
    assert m.BEACON_STALE_S >= 2 * m.BEACON_REFRESH_S


def test_cots_resolution():
    """C-B4 resolution order with the target paths (fsim_env.bat): explicit executable ->
    install root -> PATH -> conventional location (newest first) -> unresolved.
    Paths compared with backslashes so the test is independent of the host OS (field defect D-5)."""
    exe = cots.EXE_NAME
    root = r"C:\Program Files\FlightGear 2024.1"
    target = root + "\\bin\\" + exe
    old = r"C:\Program Files\FlightGear 2020.3" + "\\bin\\" + exe
    files = {target, old}
    norm = lambda p: p.replace("/", "\\") if p else p
    isfile = lambda p: norm(p) in files
    none = lambda n: None
    # explicit executable wins (used verbatim), even if a root is also set
    assert cots.resolve_executable(env={"FSIM_FGFS_EXE": target, "FSIM_FGFS_ROOT": "X:\\other"}, which=none,
                                   isfile=isfile, globber=lambda p: []) == target
    # explicit executable that does not exist -> unresolved (reported), no silent fallback
    assert cots.resolve_executable(env={"FSIM_FGFS_EXE": "X:\\missing\\fgfs.exe"}, which=none,
                                   isfile=isfile, globber=lambda p: []) is None
    # install root next: <root>\bin\fgfs(.exe)
    assert norm(cots.resolve_executable(env={"FSIM_FGFS_ROOT": root}, which=lambda n: "P:\\path\\" + exe,
                                        isfile=isfile, globber=lambda p: [])) == target
    # PATH next
    assert cots.resolve_executable(env={}, which=lambda n: "P:\\path\\" + exe, isfile=isfile,
                                   globber=lambda p: []) == "P:\\path\\" + exe
    # conventional install location, newest version first
    dirs = [r"C:\Program Files\FlightGear 2020.3", root]
    got = cots.resolve_executable(env={}, which=none, isfile=isfile,
                                  globber=lambda p: dirs if "(x86)" not in p else [], platform="win32")
    assert norm(got) == target
    # unresolved
    assert cots.resolve_executable(env={}, which=none, isfile=lambda p: False, globber=lambda p: []) is None


def test_fg_command_line():
    argv, warns = cots.build_command("fgfs", "EDDM", env={"FSIM_FGFS_AIRCRAFT_DIR": "/nope"}, isdir=lambda p: False)
    assert argv[0] == "fgfs"
    assert "--fdm=null" in argv
    assert "--native-fdm=socket,in,60,,5550,udp" in argv
    assert "--airport=EDDM" in argv and "--httpd=8080" in argv and "--timeofday=noon" in argv
    assert not any(a.startswith("--fg-aircraft") for a in argv) and warns
    logs = []
    assert cots.launch("EDDM", logs.append, env={}, resolver=lambda env=None: None) is None
    assert "NOT CONFIGURED" in logs[0]
    def boom(argv):
        raise OSError("x")
    assert cots.launch("EDDM", logs.append, env={}, popen=boom, resolver=lambda env=None: "fgfs") is None
