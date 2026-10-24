"""
tests/test_config.py
====================
Deployment-configuration rows: the FlightGear install location is read
from the environment, and the D6 command line is unaffected by it.

Added by amendment **A-6** of docs/VERIFICATION_PLAN.md, raised by a field
report from the first start on the target machine ("It crashes when trying
to launch FlightGear, most likely because it lacks the reference to the
actual .exe path on the local machine").

These rows protect two things:
  * L2SYS-REQ-01.10b "LAUNCH 3D OTW" must remain executable on a machine
    where FlightGear is NOT on PATH, and must fail informatively rather
    than crash when FlightGear is absent altogether;
  * [a: D6] the command line stays byte-for-byte the Decision Annex one
    from argv[1] onwards, whatever the local install path is.
"""

from __future__ import annotations

import os
import stat

import pytest

from flightsim.common import config as fg_config
from flightsim.common.config import (
    D6_AIRCRAFT_MODEL, ENV_FG_AIRCRAFT_DIR, ENV_FG_EXECUTABLE, ENV_FG_HOME,
    ENV_FG_HOME_LEGACY, ENV_FG_ROOT, ENV_FILE_NAME, FG_PROGRAM_NAME,
    find_env_file, inspect_aircraft_dir, parse_env_file, resolve_flightgear,
)
from flightsim.pcs.main_window import (
    FG_AIRCRAFT_MODEL, build_flightgear_command,
)


def _fake_fgfs(directory, name=None):
    """Create an executable stand-in for fgfs on disk. [c] test aid."""
    os.makedirs(directory, exist_ok=True)
    name = name or ("fgfs.exe" if os.name == "nt" else "fgfs")
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def same_path(a, b):
    """Compare two paths the way the FILESYSTEM does.

    On Windows `shutil.which` returns the extension cased as PATHEXT spells
    it (`fgfs.EXE`), which is the same file as `fgfs.exe`. Comparing the raw
    strings made a test fail on the target machine for a difference the OS
    does not consider one. [c] test aid.
    """
    return os.path.normcase(os.path.abspath(a)) == \
        os.path.normcase(os.path.abspath(b))


#: An environment with nothing set and an empty PATH, so each test states
#: exactly which variables it relies on.
#:
#: NOTE the companion `search_well_known=False` on every "nothing is
#: configured" call below: step 4 of the resolution globs the REAL
#: filesystem and an injected mapping cannot switch it off. Without the
#: flag, four of these tests silently passed on a machine with no
#: FlightGear and failed on the target machine, which HAS one - the
#: hermeticity hole the target-machine run exposed.
EMPTY_ENV = {"PATH": ""}


# ==========================================================================
# Environment-variable contract
# ==========================================================================

def test_a6_environment_variable_names_are_the_documented_ones():
    """Amendment A-6: the deployment contract is these four variables.
    Renaming one silently would break every operator's fsim_env.bat."""
    assert ENV_FG_EXECUTABLE == "FSIM_FG_EXECUTABLE"
    assert ENV_FG_HOME == "FSIM_FG_HOME"
    assert ENV_FG_AIRCRAFT_DIR == "FSIM_FG_AIRCRAFT_DIR"
    assert ENV_FG_ROOT == "FSIM_FG_ROOT"
    assert ENV_FG_HOME_LEGACY == "FG_HOME"


def test_a6_explicit_executable_wins_and_is_used_verbatim(tmp_path):
    """L2SYS-REQ-01.10b (LAUNCH 3D OTW must work on this machine),
    amendment A-6: FSIM_FG_EXECUTABLE has the highest precedence and is
    never second-guessed - an operator who states the path gets that path."""
    exe = _fake_fgfs(str(tmp_path / "wherever"), name="my-fgfs")
    other = str(_fake_fgfs(str(tmp_path / "elsewhere" / "bin")))
    env = dict(EMPTY_ENV, **{ENV_FG_EXECUTABLE: exe,
                             ENV_FG_HOME: str(tmp_path / "elsewhere")})
    config = resolve_flightgear(env)
    assert config.executable == exe
    assert config.executable != other
    assert config.found is True
    assert ENV_FG_EXECUTABLE in config.source


def test_a6_install_root_derives_bin_fgfs(tmp_path):
    """L2SYS-REQ-01.10b, amendment A-6: FSIM_FG_HOME ->
    <root>\\bin\\fgfs.exe, which is what a stock Windows FlightGear install
    looks like - the case that failed in the field."""
    root = tmp_path / "FlightGear 2020.3"
    exe = _fake_fgfs(str(root / "bin"))
    config = resolve_flightgear(dict(EMPTY_ENV, **{ENV_FG_HOME: str(root)}))
    assert config.executable == exe
    assert config.found is True


def test_a6_conventional_fg_home_is_honoured_at_lower_precedence(tmp_path):
    """Amendment A-6 [c]: an operator who already exports FG_HOME should not
    have to duplicate it, but FSIM_FG_HOME must win when both are set."""
    legacy = tmp_path / "legacy"
    preferred = tmp_path / "preferred"
    legacy_exe = _fake_fgfs(str(legacy / "bin"))
    preferred_exe = _fake_fgfs(str(preferred / "bin"))

    only_legacy = resolve_flightgear(
        dict(EMPTY_ENV, **{ENV_FG_HOME_LEGACY: str(legacy)}))
    assert only_legacy.executable == legacy_exe

    both = resolve_flightgear(dict(EMPTY_ENV, **{
        ENV_FG_HOME_LEGACY: str(legacy), ENV_FG_HOME: str(preferred)}))
    assert both.executable == preferred_exe


def test_a6_path_is_used_when_no_variable_is_set(tmp_path):
    """L2SYS-REQ-01.10b, amendment A-6: the PATH remains a valid way to
    deploy (it is how the pre-fix build worked, on the machines where it
    happened to work)."""
    bin_dir = tmp_path / "onpath"
    exe = _fake_fgfs(str(bin_dir))
    config = resolve_flightgear(dict(EMPTY_ENV, PATH=str(bin_dir)),
                                search_well_known=False)
    # compared with normcase: on Windows shutil.which returns the PATHEXT
    # casing ("fgfs.EXE"), which is the same file
    assert same_path(config.executable, exe), \
        f"{config.executable!r} is not {exe!r}"
    assert config.source == "PATH"
    assert config.found is True


def test_a6_falls_back_to_the_d6_program_name_when_nothing_is_configured():
    """Amendment A-6 + [a: D6]: with nothing configured the resolver still
    yields the Decision Annex program name, so the command line remains
    buildable and assertable on a machine without FlightGear - it is the
    `found` flag, not an exception, that reports the missing install."""
    config = resolve_flightgear(dict(EMPTY_ENV), search_well_known=False)
    assert config.executable == FG_PROGRAM_NAME == "fgfs"
    assert config.found is False
    assert ENV_FG_EXECUTABLE in config.source


def test_a6_missing_install_is_reported_not_raised(tmp_path):
    """L2SYS-REQ-01.10b, amendment A-6: the failure mode of the field
    report. A missing FlightGear must produce an actionable problem list
    naming the variable to set, never an exception."""
    config = resolve_flightgear(dict(EMPTY_ENV), search_well_known=False)
    blocking = config.blocking_problems()
    assert blocking, "a missing FlightGear executable must be reported"
    assert any(ENV_FG_EXECUTABLE in p for p in blocking)
    assert any(ENV_FG_HOME in p for p in blocking)
    assert config.launchable is False
    # describe() must be safe to print in the PCS message window
    assert any("exists on disk" in line for line in config.describe())


def test_a6_aircraft_dir_comes_from_the_environment(tmp_path):
    """Amendment A-6: the --fg-aircraft directory [a: D6] is a deployment
    parameter too."""
    aircraft = tmp_path / "Aircraft"
    aircraft.mkdir()
    config = resolve_flightgear(
        dict(EMPTY_ENV, **{ENV_FG_AIRCRAFT_DIR: str(aircraft)}))
    assert config.aircraft_dir == str(aircraft)
    assert config.aircraft_dir_exists is True

    exe = _fake_fgfs(str(tmp_path / "fg" / "bin"))
    missing = resolve_flightgear(dict(EMPTY_ENV, **{
        ENV_FG_EXECUTABLE: exe,
        ENV_FG_AIRCRAFT_DIR: str(tmp_path / "nope")}), search_well_known=False)
    assert missing.aircraft_dir_exists is False
    assert any(ENV_FG_AIRCRAFT_DIR in w for w in missing.warnings())


def test_a6_missing_aircraft_dir_is_a_warning_not_a_blocker(tmp_path):
    """L2SYS-REQ-01.10b, amendment A-6 (severity, raised by the
    target-machine run): a missing --fg-aircraft directory must NOT stop
    LAUNCH 3D OTW.

    FlightGear starts anyway and falls back to a default aircraft; refusing
    to launch would leave an operator with a correctly installed FlightGear
    unable to see anything at all. Only a missing EXECUTABLE is blocking.
    """
    exe = _fake_fgfs(str(tmp_path / "fg" / "bin"))
    config = resolve_flightgear(dict(EMPTY_ENV, **{
        ENV_FG_EXECUTABLE: exe,
        ENV_FG_AIRCRAFT_DIR: str(tmp_path / "absent")}),
        search_well_known=False)
    assert config.found is True
    assert config.blocking_problems() == []
    assert config.launchable is True
    assert len(config.warnings()) == 1
    assert config.problems() == config.warnings()


def test_a6_well_known_locations_are_searched_only_as_a_last_resort(tmp_path):
    """Amendment A-6: step 4 exists so an operator who has not configured
    anything still gets a working LAUNCH 3D OTW (it is what found
    FlightGear 2024.1 on the target machine). It must run only AFTER the
    environment and the PATH, and must be suppressible for hermetic tests.
    """
    exe = _fake_fgfs(str(tmp_path / "explicit"))
    configured = resolve_flightgear(
        dict(EMPTY_ENV, **{ENV_FG_EXECUTABLE: exe}), search_well_known=True)
    assert same_path(configured.executable, exe), \
        "the well-known scan overrode an explicitly configured path"

    suppressed = resolve_flightgear(dict(EMPTY_ENV), search_well_known=False)
    assert suppressed.executable == FG_PROGRAM_NAME


def test_a6_blank_variables_are_treated_as_unset(tmp_path):
    """Amendment A-6 [b]: `set FSIM_FG_EXECUTABLE=` in a batch file leaves an
    empty string, which must not be mistaken for a configured path."""
    config = resolve_flightgear(
        dict(EMPTY_ENV, **{ENV_FG_EXECUTABLE: "   ", ENV_FG_HOME: ""}),
        search_well_known=False)
    assert config.executable == FG_PROGRAM_NAME


def test_a6_quoted_windows_path_is_accepted(tmp_path):
    """Amendment A-6 [b]: operators paste quoted paths into fsim_env.bat;
    the surrounding quotes must be stripped."""
    exe = _fake_fgfs(str(tmp_path / "Program Files" / "FlightGear" / "bin"))
    config = resolve_flightgear(
        dict(EMPTY_ENV, **{ENV_FG_EXECUTABLE: f'"{exe}"'}))
    assert config.executable == exe
    assert config.found is True


# ==========================================================================
# D6 command line is unaffected by the deployment parameters
# ==========================================================================

def test_d6_command_line_is_unchanged_from_argv1_onwards(tmp_path):
    """[a: D6] the FG launch command line, verbatim. Only argv[0] carries
    the local path; every flag after it must be exactly the Decision Annex
    line, whatever the install location is."""
    d6_flags = [
        "--fdm=null",
        "--native-fdm=socket,in,60,,5550,udp",
        "--fg-aircraft=/opt/fg/Aircraft",
        "--aircraft=YF-16",
        "--airport=LIML",
        "--timeofday=noon",
        "--httpd=8080",
        "--prop:/input/joysticks/js[0]=0",
        "--prop:/input/joysticks/js[1]=0",
    ]
    for executable in ("fgfs",
                       r"C:\Program Files\FlightGear 2020.3\bin\fgfs.exe",
                       "/usr/games/fgfs"):
        command = build_flightgear_command(
            "LIML", "/opt/fg/Aircraft", executable=executable)
        assert command[0] == executable
        assert command[1:] == d6_flags
    assert FG_AIRCRAFT_MODEL == "YF-16"


def test_d6_fg_root_is_absent_unless_configured(tmp_path):
    """[a: D6] the nominal command line has no --fg-root; [b] it is appended
    only when FSIM_FG_ROOT is set, as a deployment escape hatch."""
    nominal = build_flightgear_command("LIML", "/ac", executable="fgfs")
    assert not any(c.startswith("--fg-root") for c in nominal)

    with_root = build_flightgear_command("LIML", "/ac", executable="fgfs",
                                         fg_root=r"C:\FG\data")
    assert with_root[:-1] == nominal
    assert with_root[-1] == r"--fg-root=C:\FG\data"


def test_d6_resolved_executable_is_used_when_none_is_passed(tmp_path,
                                                            monkeypatch):
    """L2SYS-REQ-01.10b and [a: D6], amendment A-6:
    build_flightgear_command() with no explicit executable uses the
    environment-resolved path - this is the exact line that was broken in
    the field (bare "fgfs", not on PATH after a stock Windows install)."""
    exe = _fake_fgfs(str(tmp_path / "FG" / "bin"))
    monkeypatch.setenv(ENV_FG_EXECUTABLE, exe)
    command = build_flightgear_command("EDDF", "/ac")
    assert command[0] == exe
    assert command[5] == "--airport=EDDF"


# ==========================================================================
# Aircraft-directory semantics  (amendment A-8)
# ==========================================================================

def _aircraft_tree(root, model=D6_AIRCRAFT_MODEL, nested=True):
    """Build a FlightGear aircraft layout. [c] test aid.

    nested=True  -> <root>/<model>/<model>-set.xml   (what --fg-aircraft wants)
    nested=False -> <root>/<model>-set.xml           (one level too deep)
    """
    target = os.path.join(root, model) if nested else root
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, model + "-set.xml"), "w",
              encoding="utf-8") as fh:
        fh.write("<PropertyList/>\n")
    return root


def test_a8_aircraft_dir_is_the_parent_of_the_aircraft_folder(tmp_path):
    """[a: D6] "--fg-aircraft=<dir>" with amendment A-8.

    FlightGear's documentation: "Each directory of the list in question
    should contain directories that themselves contain aircraft -set.xml
    files". So the YF-16 is discoverable when the variable names the
    PARENT directory, and FlightGear scans one level below it.
    """
    root = _aircraft_tree(str(tmp_path / "FlightGearAircraft"))
    models, hint = inspect_aircraft_dir(root)
    assert D6_AIRCRAFT_MODEL in models
    assert hint is None


def test_a8_pointing_one_level_too_deep_is_diagnosed(tmp_path):
    """Amendment A-8: pointing FSIM_FG_AIRCRAFT_DIR at an individual
    aircraft folder is the job of --aircraft-dir, not --fg-aircraft, and
    is easy to do by accident. It must be diagnosed with the fix, not
    silently accepted.
    """
    root = str(tmp_path / "FlightGearAircraft")
    _aircraft_tree(root, nested=False)          # set.xml directly in root
    models, hint = inspect_aircraft_dir(root)
    assert models == ()
    assert hint is not None
    assert "PARENT" in hint
    assert ENV_FG_AIRCRAFT_DIR in hint
    # the hint must name the parent directory as a PLAIN path. Formatting
    # it with !r would escape every backslash ("C:\\Users\\...") and the
    # operator would read that as a corrupted path - reported from the
    # target machine, and the reason this assertion is a substring test on
    # the raw path rather than on its repr.
    parent = os.path.dirname(os.path.normpath(root))
    assert parent in hint, f"hint does not name {parent} plainly: {hint}"
    assert "\\\\" not in hint, "path in the hint was rendered with repr()"


def test_a8_undiscoverable_aircraft_is_a_warning_not_a_blocker(tmp_path):
    """L2SYS-REQ-01.10b + amendment A-8: an aircraft directory that exists
    but yields no YF-16 must WARN, never block - FlightGear still starts."""
    exe = _fake_fgfs(str(tmp_path / "fg" / "bin"))
    empty = tmp_path / "Aircraft"
    empty.mkdir()
    config = resolve_flightgear(dict(EMPTY_ENV, **{
        ENV_FG_EXECUTABLE: exe, ENV_FG_AIRCRAFT_DIR: str(empty)}),
        search_well_known=False)
    assert config.aircraft_dir_exists is True
    assert config.d6_aircraft_available is False
    assert config.launchable is True                  # never blocking
    assert config.blocking_problems() == []
    assert any(D6_AIRCRAFT_MODEL in w for w in config.warnings())


def test_a8_correct_layout_produces_no_warning_at_all(tmp_path):
    """Amendment A-8: the reference deployment - executable configured and
    the YF-16 discoverable - must be completely clean."""
    exe = _fake_fgfs(str(tmp_path / "fg" / "bin"))
    root = _aircraft_tree(str(tmp_path / "FlightGearAircraft"))
    config = resolve_flightgear(dict(EMPTY_ENV, **{
        ENV_FG_EXECUTABLE: exe, ENV_FG_AIRCRAFT_DIR: root}),
        search_well_known=False)
    assert config.d6_aircraft_available is True
    assert config.problems() == []
    assert config.launchable is True
    assert any("YF-16 available" in line for line in config.describe())


def test_a8_missing_aircraft_dir_still_reports_the_original_message(tmp_path):
    """Amendment A-8 must not lose the A-6 behaviour for a directory that
    does not exist at all."""
    exe = _fake_fgfs(str(tmp_path / "fg" / "bin"))
    config = resolve_flightgear(dict(EMPTY_ENV, **{
        ENV_FG_EXECUTABLE: exe,
        ENV_FG_AIRCRAFT_DIR: str(tmp_path / "absent")}),
        search_well_known=False)
    assert config.aircraft_dir_exists is False
    assert config.launchable is True
    assert any("not found" in w for w in config.warnings())


# ==========================================================================
# fsim_env.bat read directly as a config file  (amendment A-9)
# ==========================================================================

def _write_env_file(tmp_path, body):
    path = tmp_path / ENV_FILE_NAME
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_a9_config_file_supplies_the_settings_without_being_called(tmp_path):
    """Amendment A-9: fsim_env.bat is a SCRIPT, so it only affects the shell
    that `call`s it. A tool started from a plain prompt saw none of it and
    reported every variable as "(not set)" - which reads as a broken
    mechanism, and prompted the reviewer to ask whether both the file AND
    the environment had to be set. They do not: the file is now read
    directly, so one file is the single place to edit.
    """
    exe = _fake_fgfs(str(tmp_path / "FG" / "bin"))
    aircraft = _aircraft_tree(str(tmp_path / "FlightGearAircraft"))
    path = _write_env_file(tmp_path, f"""@echo off
REM a comment line that must be ignored
set {ENV_FG_EXECUTABLE}={exe}
set {ENV_FG_AIRCRAFT_DIR}={aircraft}
""")
    config = resolve_flightgear(dict(EMPTY_ENV), search_well_known=False,
                                env_file=path, use_env_file=True)
    assert same_path(config.executable, exe)
    assert config.aircraft_dir == aircraft
    assert config.d6_aircraft_available is True
    assert config.launchable is True
    assert config.var_origins[ENV_FG_EXECUTABLE] == ENV_FILE_NAME


def test_a9_process_environment_wins_over_the_config_file(tmp_path):
    """Amendment A-9: precedence. An explicitly exported variable (what
    `call fsim_env.bat` or launch_flightsim.bat produces) must override the
    file, so the two mechanisms never contradict each other."""
    from_file = _fake_fgfs(str(tmp_path / "file" / "bin"), name="file-fgfs")
    from_env = _fake_fgfs(str(tmp_path / "env" / "bin"), name="env-fgfs")
    path = _write_env_file(tmp_path,
                           f"set {ENV_FG_EXECUTABLE}={from_file}\n")
    config = resolve_flightgear(
        dict(EMPTY_ENV, **{ENV_FG_EXECUTABLE: from_env}),
        search_well_known=False, env_file=path, use_env_file=True)
    assert same_path(config.executable, from_env)
    assert config.var_origins[ENV_FG_EXECUTABLE] == "environment"


def test_a9_parser_ignores_comments_and_foreign_assignments(tmp_path):
    """Amendment A-9 [b]: this is a configuration reader, not a batch
    interpreter. Commented-out lines and unrelated variables must not leak
    in."""
    path = _write_env_file(tmp_path, """@echo off
REM set FSIM_FG_HOME=C:\\commented\\out
set FSIM_FG_EXECUTABLE=C:\\real\\fgfs.exe
set PATH=C:\\should\\not\\be\\imported
set SOMETHING_ELSE=nope
echo [env] configured
""")
    values = parse_env_file(path)
    assert values == {"FSIM_FG_EXECUTABLE": "C:\\real\\fgfs.exe"}
    assert "FSIM_FG_HOME" not in values          # it was commented out
    assert "PATH" not in values                  # not one of ours
    assert "SOMETHING_ELSE" not in values


def test_a9_parser_expands_percent_variables(tmp_path, monkeypatch):
    """Amendment A-9 [b]: cmd.exe expands %USERPROFILE% when it runs the
    file, so the direct reader must expand it too - the reference
    deployment uses %USERPROFILE%\\Documents\\FlightGearAircraft."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    path = _write_env_file(
        tmp_path, f"set {ENV_FG_AIRCRAFT_DIR}=%USERPROFILE%\\Aircraft\n")
    values = parse_env_file(path)
    assert values[ENV_FG_AIRCRAFT_DIR].startswith(str(tmp_path / "home"))
    assert "%" not in values[ENV_FG_AIRCRAFT_DIR]


def test_a9_unexpandable_variable_is_dropped_not_passed_through(tmp_path,
                                                                monkeypatch):
    """Amendment A-9 [b]: a %VAR% with no value would otherwise be handed to
    FlightGear as a literal path containing '%'."""
    monkeypatch.delenv("NO_SUCH_VAR", raising=False)
    path = _write_env_file(
        tmp_path, f"set {ENV_FG_AIRCRAFT_DIR}=%NO_SUCH_VAR%\\Aircraft\n")
    assert ENV_FG_AIRCRAFT_DIR not in parse_env_file(path)


def test_a9_config_file_is_not_read_for_an_injected_environment(tmp_path):
    """Amendment A-9 + the A-7 hermeticity lesson: when a test states an
    environment, it means THAT environment. A real fsim_env.bat sitting on
    disk must not silently top it up."""
    path = _write_env_file(tmp_path,
                           f"set {ENV_FG_EXECUTABLE}=C:\\from\\file.exe\n")
    config = resolve_flightgear(dict(EMPTY_ENV), search_well_known=False,
                                env_file=path)     # use_env_file defaults off
    assert config.executable == FG_PROGRAM_NAME
    assert config.env_file is None


def test_a9_missing_config_file_is_harmless(tmp_path):
    """Amendment A-9: no fsim_env.bat is a normal state, not an error."""
    config = resolve_flightgear(dict(EMPTY_ENV), search_well_known=False,
                                env_file=str(tmp_path / "nope.bat"),
                                use_env_file=True)
    assert config.env_file == str(tmp_path / "nope.bat")
    assert config.executable == FG_PROGRAM_NAME
    assert config.var_origins == {}


def test_a10_messages_render_paths_plainly_not_as_python_reprs(tmp_path):
    """Amendment A-10: every path an operator sees must be a plain path.

    repr() escapes backslashes, so a Windows path formatted with !r comes
    out as "C:\\\\Users\\\\..." - reported from the target machine as
    "there seems to be broken paths". The paths were correct; the rendering
    was not. This row fails if !r is reintroduced anywhere in the operator-
    facing messages.
    """
    windows_like = str(tmp_path / "Program Files" / "FlightGear 2024.1")
    config = resolve_flightgear(dict(EMPTY_ENV, **{
        ENV_FG_EXECUTABLE: os.path.join(windows_like, "bin", "fgfs.exe"),
        ENV_FG_AIRCRAFT_DIR: os.path.join(windows_like, "Aircraft")}),
        search_well_known=False)
    rendered = " ".join(config.problems() + config.describe())
    assert rendered, "nothing was reported for a wholly absent install"
    assert windows_like in rendered, "the real path is not shown plainly"

    # Platform-independent: repr() of a path is exactly what !r emits.
    # On Windows it doubles every backslash; on POSIX it merely adds
    # single quotes. Asserting its ABSENCE catches !r on BOTH, whereas a
    # backslash test only fires on Windows - i.e. only on the reviewer's
    # machine and never on the developer's, which is how the defect
    # reached the field in the first place.
    assert repr(config.aircraft_dir) not in rendered, \
        "the aircraft directory was rendered with repr()"
    assert repr(config.executable) not in rendered, \
        "the executable path was rendered with repr()"
    # positive form: operator-facing paths are wrapped in plain quotes
    assert f'"{config.aircraft_dir}"' in rendered
    assert f'"{config.executable}"' in rendered
