"""Visual-system (FlightGear) location and command line. [a: Behaviour B4]

Resolution order: FSIM_FGFS_EXE (explicit executable) -> FSIM_FGFS_ROOT (install root)
-> PATH -> conventional install location (newest first) -> unresolved (None).
No version number of the tool appears here. [a: Conv]
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys

from flightsim.common import ports

EXE_NAME = "fgfs.exe" if sys.platform.startswith("win") else "fgfs"
DEFAULT_AIRCRAFT = "f16"  # [a: fsim_TA_PCS_ThreadFlightSim_UIM.sysml#ssfLaunchOtwView "aircraft type (e.g. f16)"]; override FSIM_FGFS_AIRCRAFT [c]


def _conventional_dirs(platform=sys.platform):
    if platform.startswith("win"):
        pats = [r"C:\Program Files\FlightGear*", r"C:\Program Files (x86)\FlightGear*"]
    elif platform == "darwin":
        pats = ["/Applications/FlightGear*.app/Contents/MacOS"]
    else:
        pats = ["/usr/games", "/usr/local/games", "/opt/flightgear*"]
    return pats


def _version_key(path):
    nums = [int(n) for n in re.findall(r"\d+", os.path.basename(path.rstrip("\\/")))]
    return nums or [0]


def resolve_executable(env=None, which=shutil.which, isfile=os.path.isfile,
                       globber=glob.glob, platform=sys.platform):
    """Return the executable path or None. All machine access injectable. [a: Conv]"""
    env = os.environ if env is None else env
    exe = env.get("FSIM_FGFS_EXE")
    if exe:
        return exe if isfile(exe) else None
    root = env.get("FSIM_FGFS_ROOT")
    if root:
        for cand in (os.path.join(root, "bin", EXE_NAME), os.path.join(root, EXE_NAME)):
            if isfile(cand):
                return cand
    found = which(EXE_NAME)
    if found:
        return found
    dirs = []
    for pat in _conventional_dirs(platform):
        dirs.extend(globber(pat))
    for d in sorted(dirs, key=_version_key, reverse=True):
        for cand in (os.path.join(d, "bin", EXE_NAME), os.path.join(d, EXE_NAME)):
            if isfile(cand):
                return cand
    return None


def build_command(exe, airport_icao, env=None, isdir=os.path.isdir):
    """Argument vector built in one place from named parts. [a: Behaviour B4]
    Argument syntax is FlightGear's own [c]; settled by `fgfs --help --verbose`."""
    env = os.environ if env is None else env
    fdm_rate = int(ports.NATIVE_FDM_RATE_HZ)
    parts = {
        "fdm": "--fdm=null",  # FDM disabled in the visual system [a: Behaviour B4]
        "native": f"--native-fdm=socket,in,{fdm_rate},,{ports.NATIVE_FDM_PORT},udp",
        "aircraft": f"--aircraft={env.get('FSIM_FGFS_AIRCRAFT', DEFAULT_AIRCRAFT)}",
        "airport": f"--airport={airport_icao}",
        "time": "--timeofday=noon",
        "httpd": f"--httpd={ports.FG_HTTPD_PORT}",
        "js0": "--prop:/input/joysticks/js[0]=0",  # joystick inputs disabled [a: Behaviour B4] syntax [c]
        "js1": "--prop:/input/joysticks/js[1]=0",
    }
    argv = [exe, parts["fdm"], parts["native"]]
    acdir = env.get("FSIM_FGFS_AIRCRAFT_DIR")
    warnings = []
    if acdir:
        if isdir(acdir):
            argv.append(f"--fg-aircraft={acdir}")
        else:
            warnings.append(f"FSIM_FGFS_AIRCRAFT_DIR not found: {acdir}; default aircraft path used")
    argv += [parts["aircraft"], parts["airport"], parts["time"], parts["httpd"], parts["js0"], parts["js1"]]
    return argv, warnings


def launch(airport_icao, log, env=None, popen=subprocess.Popen, resolver=resolve_executable):
    """Launch the visual system. Never raises; reports through `log`. [a: Behaviour B4]"""
    try:
        exe = resolver(env=env)
        if not exe:
            log("VISUAL SYSTEM NOT CONFIGURED: set FSIM_FGFS_EXE or FSIM_FGFS_ROOT.")
            return None
        argv, warns = build_command(exe, airport_icao, env=env)
        for w in warns:
            log("WARNING: " + w)
        log(f"LAUNCHING FLIGHTGEAR 3D ENGINE AT {airport_icao}...")
        return popen(argv)
    except Exception as e:  # launch never raises [a: Behaviour B4]
        log(f"VISUAL SYSTEM LAUNCH FAILED: {e}")
        return None
