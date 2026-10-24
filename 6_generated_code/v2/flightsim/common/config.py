"""FlightGear location and optionality, and the D6 launch command.

[a: D16] "No installation and no version is assumed. Location from the
environment: FSIM_FG_EXECUTABLE (full path, used verbatim, highest precedence),
FSIM_FG_HOME (install root, <root>\\bin\\fgfs.exe derived), FSIM_FG_AIRCRAFT_DIR
(the --fg-aircraft parent), FSIM_FG_ROOT (optional data directory). Resolution
order: explicit executable -> install root -> PATH -> scan of
C:\\Program Files\\FlightGear*\\bin\\fgfs.exe newest first, ordered on the version
in the directory name -> the bare program name of D6, reported as unresolved.
Values are seeded in fsim_env.bat, which the launchers `call` and the code
parses directly; an exported variable beats the file. A missing executable
blocks only LAUNCH 3D OTW and reports the variable to set; a missing aircraft
directory warns."

Hermeticity [a: D18]. Every entry point here takes its environment, its search
path and its filesystem predicates as **parameters**. Called with explicit
arguments -- which is how the suite calls it -- this module never reads
`os.environ`, never reads the real `PATH`, and never touches the filesystem.
The `None` defaults, used only by the application, fall back to the real ones.

No FlightGear version number appears anywhere in this module. [a: Sec.5.3]
"""

from __future__ import annotations

import ntpath
import os
import re
import shlex
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

#: [a: D16] the four variables, in precedence order for the two that locate the
#: executable.
ENV_EXECUTABLE = "FSIM_FG_EXECUTABLE"
ENV_HOME = "FSIM_FG_HOME"
ENV_AIRCRAFT_DIR = "FSIM_FG_AIRCRAFT_DIR"
ENV_ROOT = "FSIM_FG_ROOT"

ENV_NAMES = (ENV_EXECUTABLE, ENV_HOME, ENV_AIRCRAFT_DIR, ENV_ROOT)

#: [a: D6] "argv[0] is the executable resolved per D16" and, when nothing
#: resolves, "the bare program name of D6, reported as unresolved" [a: D16].
FGFS_PROGRAM_NAME = "fgfs"

#: [a: D16] "<root>\\bin\\fgfs.exe derived"
_BIN_SUBPATH = ("bin", "fgfs.exe")

#: [a: D16] "scan of C:\\Program Files\\FlightGear*\\bin\\fgfs.exe"
PROGRAM_FILES_GLOB_ROOT = r"C:\Program Files"
PROGRAM_FILES_PREFIX = "FlightGear"

#: [a: D6] "--aircraft=YF-16"; "FlightGear scans one level below it for
#: YF-16/YF-16-set.xml". Reported in docs/MODEL_INTAKE.md F-3: the reference
#: screenshot's menu bar reads VF-16. A Decision outranks the screenshot on
#: data (Appendix A rule 2), so this is YF-16.
AIRCRAFT_NAME = "YF-16"
AIRCRAFT_SET_FILE = "YF-16-set.xml"

#: [a: D6] JSBSim model name for the physics side.
JSBSIM_MODEL = "f16"


# ---------------------------------------------------------------------------
# fsim_env.bat parsing
# ---------------------------------------------------------------------------

# `set NAME=value`, optionally `set "NAME=value"`, case-insensitive on `set`,
# with `rem`/`::` comments and blank lines ignored. [b: D16] -- D16 says the
# code parses the file directly but does not give a grammar; this is the subset
# fsim_env.bat.template uses. [c] on anything outside that subset: a malformed
# line is ignored rather than raising, because a hand-edited env file must not
# be able to stop the application from starting.
_SET_LINE = re.compile(
    r"""^\s*set\s+          # the set keyword
        "?                  # optional opening quote (set "NAME=value")
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)
        \s*=\s*
        (?P<value>.*?)      # the value, non-greedy
        "?\s*$              # optional closing quote
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `set NAME=value` lines out of an fsim_env.bat. [a: D16]

    Comments (`rem`, `::`, `#`), blank lines and anything that is not a `set`
    assignment are ignored. Only the four FSIM_FG_* names are returned; a
    stray `set PATH=...` in a hand-edited file cannot reach the resolver.
    [b: D16, D18]
    """
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("rem ", "rem\t", "::", "#", "@rem")) or low == "rem":
            continue
        m = _SET_LINE.match(line)
        if not m:
            continue
        name = m.group("name").upper()
        if name not in ENV_NAMES:
            continue
        value = m.group("value").strip().strip('"')
        if value:
            found[name] = value
    return found


def load_env(
    env: Mapping[str, str] | None = None,
    env_file_text: str | None = None,
) -> dict[str, str]:
    """Merge the file's settings with the process environment. [a: D16]

    "an exported variable beats the file" -- so the process environment is
    applied *after* the file and wins on collision.
    """
    merged: dict[str, str] = {}
    if env_file_text:
        merged.update(parse_env_file(env_file_text))
    source = os.environ if env is None else env
    for name in ENV_NAMES:
        value = source.get(name)
        if value:
            merged[name] = value
    return merged


def read_env_file(path: str, opener: Callable[[str], str] | None = None) -> str | None:
    """Read an fsim_env.bat if it exists. Returns None if it does not. [b: D16]"""
    read = opener or _default_read
    try:
        return read(path)
    except OSError:
        return None


def _default_read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Version ordering for the Program Files scan
# ---------------------------------------------------------------------------

_VERSION_IN_NAME = re.compile(r"(\d+(?:\.\d+)*)")


def version_key(directory_name: str) -> tuple:
    """Sort key from the version embedded in a directory name. [a: D16]

    "ordered on the version in the directory name". A name with no version
    sorts below every name that has one, so an unversioned `FlightGear`
    directory is used only when nothing better exists.
    """
    m = _VERSION_IN_NAME.search(directory_name)
    if not m:
        return (0,)
    return (1,) + tuple(int(p) for p in m.group(1).split("."))


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

#: How the executable was found. Ordered as D16 orders the search.
SOURCE_EXECUTABLE_VAR = "FSIM_FG_EXECUTABLE"
SOURCE_HOME_VAR = "FSIM_FG_HOME"
SOURCE_PATH = "PATH"
SOURCE_PROGRAM_FILES = "program-files-scan"
SOURCE_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class FlightGearConfig:
    """The resolved FlightGear situation. [a: D16]

    `resolved` False means *LAUNCH 3D OTW* is the only thing blocked; the
    application and the whole suite run normally. [a: Sec.5.3, Sec.3 criterion 6]
    """

    executable: str
    resolved: bool
    source: str
    aircraft_dir: str | None = None
    fg_root: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def missing_variable_hint(self) -> str:
        """The message a blocked LAUNCH 3D OTW reports. [a: D16]

        "A missing executable blocks only LAUNCH 3D OTW and reports the
        variable to set".
        """
        return (
            f"FlightGear was not found. Set {ENV_EXECUTABLE} to the full path of "
            f"fgfs.exe, or {ENV_HOME} to the FlightGear install root, in "
            f"fsim_env.bat or in the environment."
        )


def _default_isfile(path: str) -> bool:
    return os.path.isfile(path)


def _default_isdir(path: str) -> bool:
    return os.path.isdir(path)


def _default_listdir(path: str) -> list[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


def resolve_flightgear(
    env: Mapping[str, str] | None = None,
    env_file_text: str | None = None,
    path_dirs: Sequence[str] | None = None,
    program_files_root: str = PROGRAM_FILES_GLOB_ROOT,
    isfile: Callable[[str], bool] | None = None,
    isdir: Callable[[str], bool] | None = None,
    listdir: Callable[[str], Iterable[str]] | None = None,
) -> FlightGearConfig:
    """Locate FlightGear per D16, or report it unresolved.

    Every dependency on the machine is a parameter [a: D18]:

      env                 the environment mapping; `None` means os.environ
      env_file_text       contents of fsim_env.bat, or None
      path_dirs           the directories to search as PATH; `None` means the
                          real PATH. **The suite always passes this.**
      program_files_root  the directory the D16 scan walks
      isfile/isdir/listdir  filesystem predicates, injectable

    Called with all of these supplied, the function performs no I/O of its own
    and consults nothing about the host.
    """
    isfile = isfile or _default_isfile
    isdir = isdir or _default_isdir
    listdir = listdir or _default_listdir

    settings = load_env(env, env_file_text)
    warnings: list[str] = []

    # -- aircraft directory and data root ---------------------------------
    aircraft_dir = settings.get(ENV_AIRCRAFT_DIR)
    if aircraft_dir:
        # [a: D6] "--fg-aircraft takes the parent directory; FlightGear scans
        # one level below it for YF-16/YF-16-set.xml."
        expected = ntpath.join(aircraft_dir, AIRCRAFT_NAME, AIRCRAFT_SET_FILE)
        if not isdir(aircraft_dir):
            # [a: Sec.5.3] a missing aircraft directory warns; FlightGear then
            # starts with a default aircraft.
            warnings.append(
                f"{ENV_AIRCRAFT_DIR} is set to {aircraft_dir!r}, which is not a "
                f"directory. FlightGear will start with a default aircraft."
            )
        elif not isfile(expected):
            warnings.append(
                f"{AIRCRAFT_NAME} was not found under {aircraft_dir!r} "
                f"(expected {expected}). FlightGear will start with a default "
                f"aircraft."
            )

    fg_root = settings.get(ENV_ROOT) or None
    if fg_root and not isdir(fg_root):
        warnings.append(
            f"{ENV_ROOT} is set to {fg_root!r}, which is not a directory; "
            f"--fg-root will not be passed."
        )
        fg_root = None

    def done(executable: str, resolved: bool, source: str) -> FlightGearConfig:
        return FlightGearConfig(
            executable=executable,
            resolved=resolved,
            source=source,
            aircraft_dir=aircraft_dir or None,
            fg_root=fg_root,
            warnings=tuple(warnings),
        )

    # -- 1. explicit executable, used verbatim, highest precedence --------
    explicit = settings.get(ENV_EXECUTABLE)
    if explicit:
        # "used verbatim" -- so it is returned even when the file is absent;
        # only `resolved` records whether it is there. [a: D16]
        return done(explicit, isfile(explicit), SOURCE_EXECUTABLE_VAR)

    # -- 2. install root --------------------------------------------------
    home = settings.get(ENV_HOME)
    if home:
        candidate = ntpath.join(home, *_BIN_SUBPATH)
        if isfile(candidate):
            return done(candidate, True, SOURCE_HOME_VAR)
        warnings.append(
            f"{ENV_HOME} is set to {home!r} but {candidate} does not exist."
        )

    # -- 3. PATH ----------------------------------------------------------
    dirs = _path_dirs(env, path_dirs)
    for d in dirs:
        for name in ("fgfs.exe", "fgfs"):
            candidate = ntpath.join(d, name) if "\\" in d or ":" in d else os.path.join(d, name)
            if isfile(candidate):
                return done(candidate, True, SOURCE_PATH)

    # -- 4. Program Files scan, newest first ------------------------------
    entries = [
        name for name in listdir(program_files_root)
        if name.startswith(PROGRAM_FILES_PREFIX)
    ]
    for name in sorted(entries, key=version_key, reverse=True):
        candidate = ntpath.join(program_files_root, name, *_BIN_SUBPATH)
        if isfile(candidate):
            return done(candidate, True, SOURCE_PROGRAM_FILES)

    # -- 5. the bare program name, reported as unresolved -----------------
    return done(FGFS_PROGRAM_NAME, False, SOURCE_UNRESOLVED)


def _path_dirs(
    env: Mapping[str, str] | None, path_dirs: Sequence[str] | None
) -> list[str]:
    """The PATH to search.

    `path_dirs` is explicit and wins. Otherwise a `PATH` key in the supplied
    `env` mapping is used. Only when both are absent **and** `env` is None does
    this fall back to the real `os.environ['PATH']` -- so a test that passes an
    env mapping can never reach the host's PATH. [a: D18]
    """
    if path_dirs is not None:
        return list(path_dirs)
    if env is not None:
        raw = env.get("PATH", "")
        return [p for p in raw.split(os.pathsep) if p]
    raw = os.environ.get("PATH", "")
    return [p for p in raw.split(os.pathsep) if p]


# ---------------------------------------------------------------------------
# The D6 launch command
# ---------------------------------------------------------------------------
def build_fgfs_command(
    config: FlightGearConfig,
    icao: str,
    fdm_port: int,
) -> list[str]:
    """The argv of D6's launch command.

    [a: D6]
        <resolved fgfs> --fdm=null --native-fdm=socket,in,60,,5550,udp
                        [--fg-aircraft=<dir>] --aircraft=YF-16 --airport=<ICAO>
                        --timeofday=noon --httpd=8080
                        --prop:/input/joysticks/js[0]=0
                        --prop:/input/joysticks/js[1]=0
                        [--fg-root=<dir>]

    "A bracketed argument appears only when its variable is set."
    "No FlightGear version is pinned." [a: D6]

    The `60` in --native-fdm is FlightGear's expected input rate and is D6's
    literal; it matches the ~60 Hz decimation of D4. [a: D6, D4]
    """
    from . import ports as _ports  # local import keeps ports.py optional here

    argv = [config.executable]
    argv.append("--fdm=null")
    argv.append(f"--native-fdm=socket,in,60,,{fdm_port},udp")
    if config.aircraft_dir:
        argv.append(f"--fg-aircraft={config.aircraft_dir}")
    argv.append(f"--aircraft={AIRCRAFT_NAME}")
    argv.append(f"--airport={icao}")
    argv.append("--timeofday=noon")
    argv.append(f"--httpd={_ports.FLIGHTGEAR_HTTPD}")
    # [a: D6] both joystick slots explicitly disabled, so FlightGear does not
    # grab the physical HOTAS the transmitter owns.
    argv.append("--prop:/input/joysticks/js[0]=0")
    argv.append("--prop:/input/joysticks/js[1]=0")
    if config.fg_root:
        argv.append(f"--fg-root={config.fg_root}")
    return argv


def command_line(argv: Sequence[str]) -> str:
    """Readable rendering of a command, for the message log. [b]"""
    return " ".join(shlex.quote(a) for a in argv)
