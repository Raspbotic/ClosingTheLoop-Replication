"""Hermeticity and output hygiene.

Plan rows: H-1 .. H-7.

[a: D18] "No test depends on the machine it runs on. Tests do not read the real
PATH, do not touch the filesystem outside tmp_path, and do not spawn a real
external process. Every path the application writes to by default is a parameter
the suite can redirect. Deployment rows pass identically with and without
FlightGear installed."

[a: contract Sec.3 criterion 7] "A full suite run creates, modifies and deletes
nothing outside tmp_path."

H-2 is the row R-2 requires explicitly: "at least one that fails if the suite
depends on FlightGear being installed."
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from flightsim.common import config
from flightsim.pcs import main_window as main_window_mod
from flightsim.pcs.main_window import PcsPaths, build_main_window
from flightsim.pcs.physics import PhysicsThread
from flightsim.pcs.airports import SEED_AIRPORTS
from flightsim.pcs.io_manager import IOManager

REPO = Path(__file__).resolve().parent.parent
SOURCE_DIRS = (REPO / "flightsim", REPO / "tests", REPO / "tools")


def python_files():
    for directory in SOURCE_DIRS:
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


# ===========================================================================
# H-1 -- the working tree is unchanged
# ===========================================================================
def test_suite_writes_nothing_outside_tmp_path(tree_snapshot, repo_root):
    """H-1. [a: Sec.3 criterion 7] "A full suite run creates, modifies and
    deletes nothing outside tmp_path."

    `tree_snapshot` is session-scoped and taken before the first application
    module runs; this row compares the tree against it. Requesting it here
    rather than in every row means the comparison covers whatever ran first.
    """
    from conftest import _snapshot_tree

    now = _snapshot_tree(repo_root)

    created = sorted(set(now) - set(tree_snapshot))
    deleted = sorted(set(tree_snapshot) - set(now))
    modified = sorted(
        p for p in (set(now) & set(tree_snapshot))
        if now[p] != tree_snapshot[p]
    )

    assert created == [], f"the suite created: {created}"
    assert deleted == [], f"the suite deleted: {deleted}"
    assert modified == [], f"the suite modified: {modified}"


def test_every_default_write_path_is_redirectable(tmp_path):
    """H-5. [a: D18] "Every path the application writes to by default is a
    parameter the suite can redirect."

    `default_paths()` names the real home directory; the suite never calls it,
    and every writer takes its directory as an argument.
    """
    defaults = main_window_mod.default_paths()
    assert os.path.expanduser("~") in defaults.telemetry_dir
    # ...and it is not what any row uses
    redirected = main_window_mod.default_paths(str(tmp_path))
    assert str(tmp_path) in redirected.telemetry_dir
    assert str(tmp_path) in redirected.snapshot_dir
    assert str(tmp_path) in redirected.tile_cache_dir

    # every writer takes a directory parameter
    from flightsim.pcs.map_window import TileCache
    from flightsim.pcs.telemetry import TelemetryCapture

    assert "directory" in TelemetryCapture.__init__.__code__.co_varnames
    assert "directory" in TileCache.__init__.__code__.co_varnames
    from flightsim.pcs import airports

    sig = airports.load_airports.__code__.co_varnames
    assert "directory" in sig and "override_path" in sig


# ===========================================================================
# H-2 -- the suite does not depend on FlightGear being installed
# ===========================================================================
def _deployment_facts(fg: config.FlightGearConfig, paths, clock, qt_widgets):
    """The observable results of the deployment-sensitive behaviour.

    Everything here must be **identical** with and without a FlightGear-shaped
    installation present, except the two facts that are *about* FlightGear:
    whether it resolved, and whether LAUNCH 3D OTW could start it.
    """
    launched: list[list[str]] = []
    physics = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                            sleep=clock.sleep, emit_fdm=False)
    io = IOManager(clock=clock.monotonic, bind=False)
    w = qt_widgets(build_main_window(paths, physics=physics, io=io,
                                     clock=clock.monotonic,
                                     launcher=launched.append,
                                     tile_fetch=lambda url: None,
                                     flightgear=fg))
    w.ssf_run()
    for _ in range(60):
        w.tick()
    s = w.last_state

    return {
        "sim_advanced": s.sim_time_s > 0.0,
        "on_ground": s.on_ground,
        "engine_running": s.engine_running,
        "gear_down": s.gear_down,
        "gauge_count": len(w.gauges),
        "ssf_count": len(w.ssf_buttons),
        "airport_count": w.airport_combo.count(),
        "attention_empty": w.attention_label.text() == "",
        "physics_steps_positive": s.sim_time_s > 0.0,
        "lat": round(s.lat_deg, 3),
        "lon": round(s.lon_deg, 3),
    }, launched


def test_suite_does_not_depend_on_flightgear_being_installed(
    qt_widgets, tmp_path, clock, fake_flightgear_tree, clean_env
):
    """H-2. **The row R-2 names explicitly**: "at least one that fails if the
    suite depends on FlightGear being installed."
    [a: D18] "Deployment rows pass identically with and without FlightGear
    installed."

    The same deployment-sensitive behaviour is exercised twice in one session:
    once with every FSIM_FG_* variable cleared and nothing FlightGear-shaped
    anywhere, and once with a FlightGear-shaped tree inside tmp_path and the
    variables pointing at it. Every observable except the two that are *about*
    FlightGear must be identical.
    """
    def paths_for(name):
        return PcsPaths(
            telemetry_dir=str(tmp_path / name / "telemetry"),
            snapshot_dir=str(tmp_path / name / "snapshots"),
            tile_cache_dir=str(tmp_path / name / "tiles"),
            airports_dir=str(tmp_path / name),
        )

    # -- (1) no FlightGear anywhere
    without = config.resolve_flightgear(
        env={}, path_dirs=[], program_files_root=str(tmp_path / "nothing"),
    )
    assert without.resolved is False
    facts_without, launched_without = _deployment_facts(
        without, paths_for("without"), clock, qt_widgets)

    # -- (2) a FlightGear-shaped installation present
    tree = fake_flightgear_tree()
    with_fg = config.resolve_flightgear(
        env={"FSIM_FG_EXECUTABLE": tree["executable"],
             "FSIM_FG_AIRCRAFT_DIR": tree["aircraft_dir"]},
        path_dirs=[],
    )
    assert with_fg.resolved is True
    facts_with, launched_with = _deployment_facts(
        with_fg, paths_for("with"), clock, qt_widgets)

    assert facts_without == facts_with, (
        "the application behaves differently depending on whether FlightGear "
        "is installed:\n"
        + "\n".join(f"  {k}: {facts_without[k]!r} vs {facts_with[k]!r}"
                    for k in facts_without
                    if facts_without[k] != facts_with[k])
    )
    # neither run started anything: LAUNCH 3D OTW was not pressed
    assert launched_without == [] and launched_with == []


def test_the_whole_suite_runs_with_no_flightgear_configured(clean_env):
    """H-2. Nothing outside `LAUNCH 3D OTW` reads a FlightGear path.

    Asserted on the source: every reference to the FSIM_FG_* names is in
    `config.py` (which defines them), in the launch path of `main_window.py`,
    in `fsim_env.bat.template`, or in a test.
    """
    offenders = []
    for path in python_files():
        if path.name in ("config.py",) or path.parent.name == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        for name in config.ENV_NAMES:
            if name in text:
                offenders.append((path.relative_to(REPO).as_posix(), name))
    # main_window.py legitimately names them in the blocked-launch message,
    # via config.ENV_EXECUTABLE, not as a literal.
    assert offenders == [], offenders


def test_flightgear_is_only_reachable_through_the_launch_control():
    """H-2. `subprocess` appears exactly once in the package, in the
    FlightGear launcher. [a: R-8] "LAUNCH 3D OTW is the only control that may
    depend on FlightGear."
    """
    users = []
    for path in (REPO / "flightsim").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "subprocess" in text:
            users.append(path.relative_to(REPO).as_posix())
    assert users == ["flightsim/pcs/main_window.py"], users

    src = (REPO / "flightsim/pcs/main_window.py").read_text(encoding="utf-8")
    assert src.count("import subprocess") == 1
    # and the import is inside the launcher, not at module level
    tree = ast.parse(src)
    module_level = [
        a.name for node in tree.body if isinstance(node, ast.Import)
        for a in node.names
    ]
    assert "subprocess" not in module_level


# ===========================================================================
# H-3 -- no test reads the real PATH
# ===========================================================================
def test_no_test_reads_the_real_path(clean_env):
    """H-3. [a: D18] "Tests do not read the real PATH."

    Called with an explicit environment mapping and an explicit `path_dirs`,
    the resolver consults neither `os.environ` nor the real PATH -- proved by
    making both raise if touched.
    """
    class Exploding(dict):
        def get(self, *a, **k):
            raise AssertionError("os.environ was read")

        def __getitem__(self, k):
            raise AssertionError("os.environ was read")

    real_environ = os.environ
    try:
        os.environ = Exploding()          # type: ignore[assignment]
        cfg = config.resolve_flightgear(
            env={}, path_dirs=[], program_files_root="C:\\nowhere",
            isfile=lambda p: False, isdir=lambda p: False,
            listdir=lambda p: [],
        )
    finally:
        os.environ = real_environ         # type: ignore[assignment]

    assert cfg.resolved is False
    assert cfg.executable == config.FGFS_PROGRAM_NAME


def test_resolution_entry_points_take_their_environment_as_parameters():
    """H-3. The shape that makes H-3 possible. [a: D18]"""
    names = config.resolve_flightgear.__code__.co_varnames
    for parameter in ("env", "env_file_text", "path_dirs",
                      "program_files_root", "isfile", "isdir", "listdir"):
        assert parameter in names, parameter


# ===========================================================================
# H-4 -- no test spawns a real process
# ===========================================================================
def test_no_test_spawns_a_real_process(monkeypatch, qt_widgets, tmp_path,
                                       clock, fake_flightgear_tree):
    """H-4. [a: D18] "Tests do not ... spawn a real external process."

    `subprocess.Popen` is replaced by a recorder for the whole row. The launch
    path is exercised with a FlightGear-shaped tree and must reach the injected
    launcher, never Popen.
    """
    calls = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: calls.append(a) or object())

    tree = fake_flightgear_tree()
    fg = config.resolve_flightgear(
        env={"FSIM_FG_EXECUTABLE": tree["executable"]}, path_dirs=[])
    launched: list[list[str]] = []
    paths = PcsPaths(
        telemetry_dir=str(tmp_path / "t"), snapshot_dir=str(tmp_path / "s"),
        tile_cache_dir=str(tmp_path / "c"), airports_dir=str(tmp_path),
    )
    physics = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                            sleep=clock.sleep, emit_fdm=False)
    io = IOManager(clock=clock.monotonic, bind=False)
    w = qt_widgets(build_main_window(paths, physics=physics, io=io,
                                     clock=clock.monotonic,
                                     launcher=launched.append,
                                     tile_fetch=lambda url: None,
                                     flightgear=fg))
    w.ssf_launch_otw()

    assert len(launched) == 1, "the injected launcher was not used"
    assert calls == [], "a real process was spawned"


# ===========================================================================
# H-6 -- no joystick
# ===========================================================================
def test_no_test_requires_a_joystick():
    """H-6. [a: Sec.3 criterion 6] "The application and the complete suite run
    to completion with ... no HOTAS attached."

    pygame is never imported by the suite, so no row can be reading a device.
    """
    assert "pygame" not in sys.modules, (
        "pygame was imported during the suite; some row is touching a device"
    )


def test_the_application_starts_with_no_hotas(qt_widgets, tmp_path, clock):
    """H-6. The PCS constructs and flies with nothing on 5005.
    [a: Sec.3 criterion 6] [a: L2SYS-REQ-01.08b]
    """
    paths = PcsPaths(
        telemetry_dir=str(tmp_path / "t"), snapshot_dir=str(tmp_path / "s"),
        tile_cache_dir=str(tmp_path / "c"), airports_dir=str(tmp_path),
    )
    physics = PhysicsThread(airport=SEED_AIRPORTS[0], clock=clock.monotonic,
                            sleep=clock.sleep, emit_fdm=False)
    io = IOManager(clock=clock.monotonic, bind=False)
    w = qt_widgets(build_main_window(paths, physics=physics, io=io,
                                     clock=clock.monotonic,
                                     tile_fetch=lambda url: None))
    w.ssf_run()
    w.virtual_hotas.throttle.setValue(50)
    for _ in range(60):
        w.tick()
    assert w.last_state.sim_time_s > 0.0
    assert w.physics.commands.get().throttle == pytest.approx(0.5)


# ===========================================================================
# H-7 -- no FlightGear version anywhere
# ===========================================================================
def test_no_flightgear_version_appears_in_the_tree():
    """H-7. [a: Sec.5.3] "No FlightGear version number appears in the code or
    the tests."
    """
    # A year-dotted release version, which is the shape FlightGear uses.
    # No example is written out here: an example would itself be a
    # version number appearing in the tests, which is what Sec.5.3
    # forbids and what this row exists to detect.
    pattern = re.compile(r"\b(19|20)\d\d\.\d+(\.\d+)?\b")
    offenders = []
    for path in python_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if "FlightGear" not in line and "fgfs" not in line:
                continue
            m = pattern.search(line)
            if m:
                offenders.append(
                    f"{path.relative_to(REPO).as_posix()}:{lineno}: {m.group(0)}"
                )
    assert offenders == [], offenders


def test_no_flightgear_version_is_pinned_in_the_batch_files():
    """H-7. The same for the delivered scripts, where a path is most tempting.
    [a: Sec.5.3]
    """
    pattern = re.compile(r"\b(19|20)\d\d\.\d+(\.\d+)?\b")
    for name in ("setup_venvs.bat", "launch_flightsim.bat", "run_tests.bat"):
        path = REPO / name
        if not path.exists():
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if line.strip().lower().startswith(("rem", "::")):
                continue          # a commented example is documentation
            assert not pattern.search(line), f"{name}:{lineno}: {line.strip()}"


# ===========================================================================
# The suite's own conventions [a: D15]
# ===========================================================================
def test_every_test_module_cites_requirement_ids():
    """[a: D15] "Requirement ids are cited in test docstrings, or in the module
    docstring where a module verifies one requirement throughout."
    """
    pattern = re.compile(r"L2SYS-REQ-\d\d\.\d\d\w?")
    for path in sorted((REPO / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        assert pattern.search(text), (
            f"{path.name} cites no requirement id anywhere"
        )


def test_offscreen_qt_is_configured():
    """[a: D15] "Offscreen Qt (QT_QPA_PLATFORM=offscreen)"."""
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
