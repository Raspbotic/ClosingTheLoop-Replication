"""
flightsim/common/config.py
==========================
Local-installation configuration: where FlightGear lives on THIS machine.

TRACEABILITY
------------
[b] Derived from Decision D6 and the R-0 target-platform statement
    ("Target platform: Windows gamer laptop, Python 3.14 + 3.11, physical
    HOTAS, FlightGear installed"). D6 fixes the FlightGear *command line*
    verbatim; it does not state where fgfs.exe is installed, and the SysML
    v2 model has no element for a filesystem path either - the TA only
    fixes the interface (FlightGearOTW.visualEngine.e_nativeFdmIn 5550 and
    localHttpServer.e_groundElevMeterOut 8080, both [a: D4]). The install
    location is therefore a DEPLOYMENT parameter, not a design item, and
    belongs in the environment rather than in the source.
[c] The concrete environment-variable NAMES below are a developer choice;
    they are namespaced FSIM_* so they cannot collide with FlightGear's own
    variables, and the conventional FG_HOME / FG_ROOT are also honoured at
    lower precedence for operators who already set them.

WHY THIS MODULE EXISTS
----------------------
Field report (first start on the target machine): "It crashes when trying
to launch FlightGear, most likely because it lacks the reference to the
actual .exe path on the local machine." Correct diagnosis - the launch
used the bare program name `fgfs`, which only works when FlightGear's bin
directory happens to be on PATH. Windows installers do not put it there.

CONFIGURATION
-------------
Set these before launching (fsim_env.bat is the shipped template, and
launch_flightsim.bat calls it automatically when present):

    FSIM_FG_EXECUTABLE    full path to fgfs.exe - highest precedence,
                          used verbatim
    FSIM_FG_HOME          FlightGear install root; the executable is
                          derived as <root>\\bin\\fgfs.exe
    FSIM_FG_AIRCRAFT_DIR  the --fg-aircraft directory holding YF-16 [a: D6]
    FSIM_FG_ROOT          optional FlightGear data directory; when set it
                          adds --fg-root=<dir> to the command line

Resolution order for the executable (first hit wins):

    1. FSIM_FG_EXECUTABLE
    2. FSIM_FG_HOME / FG_HOME  ->  <root>/bin/fgfs[.exe]
    3. the PATH (shutil.which)
    4. the well-known Windows install locations [c]
    5. the bare name "fgfs"  [a: D6 default; kept so the command line is
       still buildable and assertable on a machine without FlightGear]

Diagnostics:

    python -m flightsim.common.config
"""

from __future__ import annotations

import glob
import ntpath
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Environment-variable names [c]
# --------------------------------------------------------------------------
ENV_FG_EXECUTABLE = "FSIM_FG_EXECUTABLE"
ENV_FG_HOME = "FSIM_FG_HOME"
ENV_FG_AIRCRAFT_DIR = "FSIM_FG_AIRCRAFT_DIR"
ENV_FG_ROOT = "FSIM_FG_ROOT"

#: [c] Conventional FlightGear variables, honoured at lower precedence so an
#: operator who already exports them does not have to duplicate them.
ENV_FG_HOME_LEGACY = "FG_HOME"
ENV_FG_ROOT_LEGACY = "FG_ROOT"

#: [a: D6] the program named by the Decision Annex command line. Kept as the
#: last-resort fallback so build_flightgear_command() still produces the D6
#: line verbatim on a machine where FlightGear is not installed.
FG_PROGRAM_NAME = "fgfs"

#: [c] Best-guess install locations, searched only after the environment and
#: the PATH. Globs, because the version is part of the directory name.
_WINDOWS_CANDIDATES = (
    # version-neutral globs: they match FlightGear 2020.3, 2024.1, and
    # whatever comes next, newest first (sorted reverse below).
    r"C:\Program Files\FlightGear*\bin\fgfs.exe",
    r"C:\Program Files (x86)\FlightGear*\bin\fgfs.exe",
    r"C:\FlightGear*\bin\fgfs.exe",
)
_POSIX_CANDIDATES = (
    "/usr/games/fgfs",
    "/usr/bin/fgfs",
    "/usr/local/bin/fgfs",
    "/Applications/FlightGear.app/Contents/MacOS/fgfs",
)


#: [b] The per-machine config file. It is a .bat because launch_flightsim.bat
#: `call`s it to populate a shell, but it is ALSO parsed directly here so the
#: same file works when a tool is started from a plain prompt that never
#: called it. One file, one place to edit, both entry points.
ENV_FILE_NAME = "fsim_env.bat"

#: Variables this module honours, in the order the CLI check prints them.
KNOWN_VARS = ("FSIM_FG_EXECUTABLE", "FSIM_FG_HOME", "FG_HOME",
              "FSIM_FG_AIRCRAFT_DIR", "FSIM_FG_ROOT", "FG_ROOT")

#: `set NAME=VALUE`, tolerating `set "NAME=VALUE"` and leading whitespace.
_SET_RE = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$',
                     re.IGNORECASE)


def find_env_file(start: Optional[str] = None) -> Optional[str]:
    """Locate fsim_env.bat: next to the package root, or in the CWD. [b]"""
    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))          # <repo root>
    for candidate in ([start] if start else []) + [
            os.path.join(os.getcwd(), ENV_FILE_NAME),
            os.path.join(here, ENV_FILE_NAME)]:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def parse_env_file(path: str) -> Dict[str, str]:
    """Read the `set NAME=VALUE` assignments of a fsim_env.bat.

    [b] Only the KNOWN_VARS are taken - this is a configuration reader, not
    a batch interpreter, and it must not import unrelated assignments.
    `REM` lines are skipped by the regex (they do not start with `set`).
    %VAR% references are expanded against the CURRENT environment, which is
    how cmd.exe would expand them; ntpath.expandvars is used explicitly so
    the expansion also works when the file is parsed on a non-Windows host.
    """
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return out
    for line in lines:
        match = _SET_RE.match(line)
        if not match:
            continue
        name, value = match.group(1).upper(), match.group(2).strip()
        if name not in KNOWN_VARS:
            continue
        value = ntpath.expandvars(value).strip().strip('"')
        if value and "%" not in value:        # unexpanded %VAR% -> unusable
            out[name] = value
    return out


def _env(name: str) -> Optional[str]:
    """Read an environment variable, treating blank as unset [b]."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip().strip('"')
    return value or None


def _exe_names() -> Tuple[str, ...]:
    return ("fgfs.exe", "fgfs") if os.name == "nt" else ("fgfs", "fgfs.exe")


def _from_home(root: str) -> Optional[str]:
    """<root>/bin/fgfs[.exe], or <root>/fgfs[.exe] if bin/ is absent [b]."""
    for sub in ("bin", ""):
        for name in _exe_names():
            candidate = os.path.join(root, sub, name) if sub \
                else os.path.join(root, name)
            if os.path.isfile(candidate):
                return candidate
    return None


@dataclass(frozen=True)
class FlightGearConfig:
    """The resolved FlightGear deployment parameters for THIS machine."""

    executable: str
    #: Where the executable came from - shown in the PCS message window.
    source: str
    #: True when the resolved executable actually exists on disk. False
    #: means the PCS will refuse to launch and say why, instead of raising.
    found: bool
    aircraft_dir: str
    aircraft_dir_exists: bool
    fg_root: Optional[str]
    #: Aircraft models FlightGear would find under aircraft_dir [b].
    aircraft_models: Tuple[str, ...] = ()
    #: Diagnosis when the directory looks one level too deep [b].
    aircraft_hint: Optional[str] = None
    #: The fsim_env.bat that was read, if any [b].
    env_file: Optional[str] = None
    #: Per-variable provenance: "environment" or "fsim_env.bat" [b].
    var_origins: Dict[str, str] = field(default_factory=dict)

    @property
    def d6_aircraft_available(self) -> bool:
        """[a: D6] Is the YF-16 visual model discoverable?"""
        return D6_AIRCRAFT_MODEL in self.aircraft_models

    def describe(self) -> List[str]:
        """Human-readable lines for the message window / the CLI check."""
        lines = [
            f"FlightGear executable : {self.executable}",
            f"  resolved from       : {self.source}",
            f"  exists on disk      : {'YES' if self.found else 'NO'}",
            f"Aircraft directory    : {self.aircraft_dir}",
            f"  exists on disk      : "
            f"{'YES' if self.aircraft_dir_exists else 'NO'}",
        ]
        if self.aircraft_dir_exists:
            lines.append(
                f"  aircraft found      : "
                f"{', '.join(self.aircraft_models) if self.aircraft_models else '(none)'}")
            lines.append(
                f"  {D6_AIRCRAFT_MODEL} available     : "
                f"{'YES' if self.d6_aircraft_available else 'NO'}")
        if self.fg_root:
            lines.append(f"FlightGear data root  : {self.fg_root}")
        return lines

    # NOTE on formatting [b]: paths in these messages are wrapped in plain
    # double quotes, never formatted with !r. repr() escapes every
    # backslash, so a Windows path rendered with !r comes out as
    # "C:\\Users\\..." - which an operator reasonably reads as a corrupted
    # path. Reported from the target machine as "there seems to be broken
    # paths"; the paths were correct, the rendering was not.

    def blocking_problems(self) -> List[str]:
        """Problems that make a launch IMPOSSIBLE.

        [b] Only a missing executable is blocking: there is nothing to
        spawn. Severity matters - see warnings() for the other case.
        """
        if self.found:
            return []
        return [
            f'FlightGear executable not found at "{self.executable}" '
            f"(resolved from {self.source}). Set {ENV_FG_EXECUTABLE} to "
            f"the full path of fgfs.exe, or {ENV_FG_HOME} to the "
            f"FlightGear install root - see fsim_env.bat."]

    def warnings(self) -> List[str]:
        """Problems worth reporting that must NOT prevent a launch.

        [b] A missing --fg-aircraft directory is one of these: FlightGear
        still starts, it simply falls back to a default aircraft instead of
        the YF-16 of D6. Refusing to launch here would leave an operator
        with a correctly installed FlightGear unable to see anything at
        all, which is worse than the degraded visual it replaces.
        """
        if not self.aircraft_dir_exists:
            return [
                f'Aircraft directory not found at "{self.aircraft_dir}": '
                f"FlightGear will start but will not find the "
                f"{D6_AIRCRAFT_MODEL} model [a: D6]. Set "
                f"{ENV_FG_AIRCRAFT_DIR} to the directory that contains it."]
        if self.d6_aircraft_available:
            return []
        out = [
            f"{D6_AIRCRAFT_MODEL} not discoverable under "
            f'"{self.aircraft_dir}" [a: D6]: FlightGear will start but may '
            f"fall back to a default aircraft."]
        if self.aircraft_hint:
            out.append(self.aircraft_hint)
        return out

    def problems(self) -> List[str]:
        """Every actionable issue, blocking first. Used by the CLI check."""
        return self.blocking_problems() + self.warnings()

    @property
    def launchable(self) -> bool:
        """True when LAUNCH 3D OTW can actually spawn something."""
        return not self.blocking_problems()


#: [b] FlightGear identifies an aircraft by a "<model>-set.xml" file.
AIRCRAFT_SET_SUFFIX = "-set.xml"

#: [a: D6] "FlightGear visual YF-16 from a user aircraft dir".
D6_AIRCRAFT_MODEL = "YF-16"


def inspect_aircraft_dir(aircraft_dir: str,
                         model: str = D6_AIRCRAFT_MODEL):
    """Report which aircraft FlightGear would actually find under a
    --fg-aircraft directory, and diagnose the commonest mis-setting.

    [b] FlightGear's own documentation is explicit about the semantics:
    "Each directory of the list in question should contain directories
    that themselves contain aircraft -set.xml files" - i.e. --fg-aircraft
    takes the PARENT directory (typically called "Aircraft"), and
    FlightGear scans ONE level of subdirectories below it. Pointing it at
    an individual aircraft folder (the job of --aircraft-dir) is therefore
    one level too deep, and is easy to do by accident.

    Returns (models, hint):
        models - model names discoverable at FlightGear's scan depth
        hint   - a human-readable diagnosis when nothing is discoverable
                 there but -set.xml files DO sit directly in the directory
    """
    models: List[str] = []
    hint: Optional[str] = None
    if not os.path.isdir(aircraft_dir):
        return tuple(models), hint

    # depth FlightGear actually scans: <dir>/<aircraft>/<model>-set.xml
    for entry in sorted(glob.glob(os.path.join(
            aircraft_dir, "*", "*" + AIRCRAFT_SET_SUFFIX))):
        models.append(os.path.basename(entry)[:-len(AIRCRAFT_SET_SUFFIX)])

    if models:
        return tuple(models), hint

    # one level too deep: the -set.xml files sit directly in this directory
    here = sorted(glob.glob(os.path.join(
        aircraft_dir, "*" + AIRCRAFT_SET_SUFFIX)))
    if here:
        names = ", ".join(os.path.basename(h)[:-len(AIRCRAFT_SET_SUFFIX)]
                          for h in here)
        parent = os.path.dirname(os.path.normpath(aircraft_dir))
        hint = (
            f'"{aircraft_dir}" contains the aircraft itself ({names}), but '
            f"--fg-aircraft expects the PARENT directory and FlightGear "
            f"scans only one level below it. If {model} is not found, set "
            f'{ENV_FG_AIRCRAFT_DIR} to "{parent}" instead.')
    return tuple(models), hint


def default_aircraft_dir() -> str:
    """[c] FlightGear's conventional per-user aircraft directory."""
    return os.path.join(os.path.expanduser("~"), "FlightGear", "Aircraft")


def resolve_flightgear(env=None,
                       search_well_known: bool = True,
                       env_file: Optional[str] = None,
                       use_env_file: Optional[bool] = None
                       ) -> FlightGearConfig:
    """Resolve the FlightGear deployment parameters from the environment.

    Two sources, in precedence order [b]:

        1. the process environment (what `call fsim_env.bat` produces, and
           what launch_flightsim.bat arranges for every window it opens);
        2. the `set NAME=VALUE` lines of fsim_env.bat itself, read directly.

    Layer 2 exists because fsim_env.bat is a *script*: it only affects the
    shell that `call`s it. A tool started from a plain prompt therefore saw
    none of it and reported every variable as "(not set)", which reads as a
    broken mechanism. Reading the same file directly makes one file the
    single place to edit, whichever way a tool is started. The environment
    still wins, so an explicitly exported variable overrides the file.

    `env` (a mapping) is injectable so the resolution order is testable
    without mutating the process environment [b]. When `env` is supplied,
    the config file is NOT read unless `use_env_file=True` is passed
    explicitly - a test that states an environment means that environment,
    and must not be silently topped up from a real file on disk (the A-7
    hermeticity lesson).

    `search_well_known` [b] controls step 4 only. That step globs the REAL
    filesystem, so it is the one part of the resolution that an injected
    `env` cannot neutralise: on a machine that HAS FlightGear installed it
    finds it no matter what the mapping says. Tests that mean "nothing is
    installed" must therefore pass False - which is exactly the hermeticity
    hole the target-machine test run exposed (a real FlightGear 2024.1
    install satisfied four tests that assumed none).
    """
    if use_env_file is None:
        use_env_file = env is None            # production yes, tests no
    base = dict(os.environ) if env is None else dict(env)

    file_values: Dict[str, str] = {}
    resolved_env_file = None
    if use_env_file:
        resolved_env_file = env_file or find_env_file()
        if resolved_env_file:
            file_values = parse_env_file(resolved_env_file)

    origins: Dict[str, str] = {}

    def get(name: str) -> Optional[str]:
        value = base.get(name)
        if value is not None:
            value = str(value).strip().strip('"')
            if value:
                origins[name] = "environment"
                return value
        value = file_values.get(name)
        if value:
            origins[name] = ENV_FILE_NAME
            return value
        return None

    executable: Optional[str] = None
    source = ""

    # 1. explicit executable - used verbatim, never second-guessed
    explicit = get(ENV_FG_EXECUTABLE)
    if explicit:
        executable, source = explicit, f"{ENV_FG_EXECUTABLE}"

    # 2. install root
    if executable is None:
        for var in (ENV_FG_HOME, ENV_FG_HOME_LEGACY):
            root = get(var)
            if root:
                derived = _from_home(root)
                if derived:
                    executable, source = derived, f"{var} -> {derived}"
                    break
                # the root is set but holds no executable: report it as the
                # source anyway so the error message points at the variable
                executable = os.path.join(root, "bin", _exe_names()[0])
                source = f"{var} (no executable under it)"
                break

    # 3. PATH  ([b] the PATH is taken from the same mapping, so the
    #    resolution order is testable without touching the real one)
    #
    #    NOTE the RAW read: unlike every other variable here, an empty PATH
    #    must mean "search nothing", not "unset". shutil.which(path=None)
    #    silently falls back to os.environ["PATH"], so normalising "" to
    #    None would quietly re-enable the real PATH and make an injected
    #    environment non-hermetic - the same class of hole that let four
    #    tests pass here and fail on the target machine.
    # `base` is the process environment in production and the injected
    # mapping in tests, so one read covers both.
    search_path = base.get("PATH")
    if executable is None and search_path:
        on_path = shutil.which(FG_PROGRAM_NAME, path=search_path)
        if on_path:
            executable, source = on_path, "PATH"

    # 4. well-known install locations [c]
    if executable is None and search_well_known:
        patterns = _WINDOWS_CANDIDATES if os.name == "nt" else _POSIX_CANDIDATES
        for pattern in patterns:
            hits = sorted(glob.glob(pattern), reverse=True)  # newest version
            if hits:
                executable, source = hits[0], "well-known install location"
                break

    # 5. the D6 default
    if executable is None:
        executable = FG_PROGRAM_NAME
        source = (f"default program name (set {ENV_FG_EXECUTABLE} or "
                  f"{ENV_FG_HOME})")

    found = os.path.isfile(executable) or bool(
        os.path.basename(executable) == executable and search_path
        and shutil.which(executable, path=search_path) is not None)

    aircraft_dir = get(ENV_FG_AIRCRAFT_DIR) or default_aircraft_dir()
    fg_root = get(ENV_FG_ROOT) or get(ENV_FG_ROOT_LEGACY)

    aircraft_models, aircraft_hint = inspect_aircraft_dir(aircraft_dir)
    return FlightGearConfig(
        executable=executable, source=source, found=found,
        aircraft_dir=aircraft_dir,
        aircraft_dir_exists=os.path.isdir(aircraft_dir),
        fg_root=fg_root, aircraft_models=aircraft_models,
        aircraft_hint=aircraft_hint, env_file=resolved_env_file,
        var_origins=origins)


def flightgear_executable(env=None, search_well_known: bool = True) -> str:
    """Convenience accessor used by build_flightgear_command()."""
    return resolve_flightgear(env, search_well_known).executable


def main(argv=None) -> int:
    """`python -m flightsim.common.config` - print the resolved deployment
    configuration and exit non-zero if anything is missing."""
    config = resolve_flightgear()
    print("=" * 66)
    print("FlightSim local deployment configuration")
    print("=" * 66)
    for line in config.describe():
        print(line)
    print()
    if config.env_file:
        print(f"Config file           : {config.env_file}")
    else:
        print(f"Config file           : no {ENV_FILE_NAME} found "
              f"(copy {ENV_FILE_NAME}.template to {ENV_FILE_NAME})")
    print()
    print("Settings (process environment first, then the config file):")
    file_values = parse_env_file(config.env_file) if config.env_file else {}
    for name in KNOWN_VARS:
        origin = config.var_origins.get(name)
        if origin == "environment":
            print(f"  {name:<22} = {os.environ.get(name)}   [environment]")
        elif origin:
            print(f"  {name:<22} = {file_values.get(name)}   [{origin}]")
        else:
            print(f"  {name:<22} = (not set)")
    blocking = config.blocking_problems()
    warnings = config.warnings()
    print()
    if blocking:
        print("BLOCKING (LAUNCH 3D OTW cannot run):")
        for problem in blocking:
            print(f"  - {problem}")
    if warnings:
        print("WARNING (LAUNCH 3D OTW will still run):")
        for warning in warnings:
            print(f"  - {warning}")
    if blocking or warnings:
        print()
        print("Edit fsim_env.bat (copy of fsim_env.bat.template) and set the "
              "paths for this machine, then re-run this check.")
    if blocking:
        return 1
    if warnings:
        print("OK - FlightGear is reachable; LAUNCH 3D OTW will run "
              "(with the warning above).")
        return 0
    print("OK - FlightGear is reachable; LAUNCH 3D OTW will work.")
    return 0


__all__ = [
    "ENV_FG_EXECUTABLE", "ENV_FG_HOME", "ENV_FG_AIRCRAFT_DIR", "ENV_FG_ROOT",
    "ENV_FG_HOME_LEGACY", "ENV_FG_ROOT_LEGACY", "FG_PROGRAM_NAME",
    "FlightGearConfig", "resolve_flightgear", "flightgear_executable",
    "default_aircraft_dir", "inspect_aircraft_dir", "D6_AIRCRAFT_MODEL",
    "AIRCRAFT_SET_SUFFIX", "ENV_FILE_NAME", "KNOWN_VARS", "find_env_file",
    "parse_env_file",
]


if __name__ == "__main__":
    sys.exit(main())
