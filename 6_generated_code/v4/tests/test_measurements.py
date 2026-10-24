"""C-MEAS: re-measure the JSBSim conventions the implementation depends on.
[a: Conv] a library upgrade that reverses one fails here. See docs/MEASUREMENTS.md."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import measure_jsbsim as M


def test_m1_cutoff_property():
    fdm = M.new_fdm()
    names = [k.split(" ")[0] for k in fdm.get_property_catalog()]
    assert "propulsion/cutoff_cmd" in names and "propulsion/fuel_freeze" in names


def test_m4_m5_control_signs():
    r = M.m_control_signs()
    assert r["fcs/elevator-cmd-norm +0.5 -> velocities/q-rad_sec"] < 0
    assert r["fcs/aileron-cmd-norm +0.5 -> velocities/p-rad_sec"] > 0
    f = M.new_fdm(alt_agl_ft=3000, speed_kts=350); f["propulsion/set-running"] = -1; M.run(f, 0.2)
    p0 = f["attitude/psi-deg"]; f["fcs/rudder-cmd-norm"] = 0.5; M.run(f, 1.5)
    assert f["attitude/psi-deg"] < p0
    g = M.new_fdm(); g["propulsion/set-running"] = -1; g["fcs/throttle-cmd-norm"] = 0.3; M.run(g, 8)
    p0 = g["attitude/psi-deg"]; g["fcs/steer-cmd-norm"] = 0.5; M.run(g, 3)
    assert g["attitude/psi-deg"] > p0 + 5


def test_m3_starter_sequence():
    f = M.new_fdm(); f["propulsion/cutoff_cmd"] = 1; f["propulsion/starter_cmd"] = 1
    M.run(f, 5)
    assert f["propulsion/engine/n2"] >= 14
    f["propulsion/cutoff_cmd"] = 0
    M.run(f, 25)
    assert f["propulsion/engine/n2"] > 50 and f["propulsion/engine/set-running"] >= 1


def test_m6_brakes_hold_idle():
    r = M.m_park_brake()
    f = M.new_fdm(); f["propulsion/set-running"] = -1
    for p in ("fcs/left-brake-cmd-norm", "fcs/right-brake-cmd-norm", "fcs/center-brake-cmd-norm"):
        f[p] = 1
    M.run(f, 10)
    assert f["velocities/vg-fps"] < 0.1


def test_m9_units():
    u = M.m_units()
    assert abs(u["h-sl-meters"] - u["h-sl-ft"] * 0.3048) < 1e-6
    assert abs(u["psi-deg"] - 81.0) < 0.1


def test_m13_fcs_doubles_throttle():
    """M13: f16 FCS maps throttle-cmd-norm 0.5 to throttle-pos-norm 1.0 (MIL, N2 100 %)."""
    f = M.new_fdm(); f["propulsion/set-running"] = -1
    for p in ("fcs/left-brake-cmd-norm", "fcs/right-brake-cmd-norm", "fcs/center-brake-cmd-norm"):
        f[p] = 1
    f["fcs/throttle-cmd-norm"] = 0.5
    M.run(f, 8)
    assert abs(f["fcs/throttle-pos-norm"] - 1.0) < 1e-6 and f["propulsion/engine/n2"] > 99
