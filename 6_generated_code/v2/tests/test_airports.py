"""Configurable airport list.

This module verifies L2SYS-REQ-01.04 throughout, on its list side: "The system
shall initialize the Virtual Ownship on ground at a user-selected airport from a
**configurable airport list**." The on-ground initialization itself is
`test_physics.py::test_initial_condition_is_on_ground_at_the_selected_airport`.

Plan rows: P-01.04-1 .. P-01.04-5.

Every row directs the override file into `tmp_path`; nothing is read from the
host. [a: D18]
"""

from __future__ import annotations

import json

import pytest

from flightsim.pcs import airports
from flightsim.pcs.airports import Airport

# [a: D13] The seed table, transcribed from the Decision Annex into this file.
D13_TABLE = [
    ("LIML", "Milano Linate", 45.4451, 9.2767, 176.0, 107.6),
    ("LIMC", "Milano Malpensa", 45.6306, 8.7281, 350.0, 234.1),
    ("KSFO", "San Francisco Intl", 37.6188, -122.3750, 284.0, 4.0),
    ("EDDF", "Frankfurt am Main", 50.0333, 8.5706, 250.0, 111.0),
]


def write_override(tmp_path, payload) -> str:
    p = tmp_path / airports.OVERRIDE_FILENAME
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


# ===========================================================================
# P-01.04-1
# ===========================================================================
def test_seed_table_is_the_d13_table():
    """P-01.04-1. Exactly the four D13 rows, every field equal to the D13
    literal. [a: D13]
    """
    loaded = airports.load_airports()
    assert len(loaded) == 4
    for got, (icao, name, lat, lon, hdg, elev) in zip(loaded, D13_TABLE):
        assert got.icao == icao
        assert got.name == name
        assert got.lat_deg == pytest.approx(lat)
        assert got.lon_deg == pytest.approx(lon)
        assert got.runway_hdg_deg == pytest.approx(hdg)
        assert got.elevation_m == pytest.approx(elev)


def test_load_with_no_arguments_reads_no_file(tmp_path, monkeypatch):
    """P-01.04-1. With no directory and no path, nothing is read from the host
    at all -- the seed table is returned. [a: D18]
    """
    def explode(*a, **k):
        raise AssertionError("load_airports() opened a file with no path given")

    monkeypatch.setattr("builtins.open", explode)
    assert len(airports.load_airports()) == 4


# ===========================================================================
# P-01.04-2
# ===========================================================================
def test_partial_override_keeps_unmentioned_seed_values(tmp_path):
    """P-01.04-2. [a: D13] "an unmentioned key keeps its seed value"."""
    path = write_override(tmp_path, {"LIML": {"elevation_m": 200.0}})
    loaded = airports.load_airports(override_path=path)
    liml = airports.find(loaded, "LIML")
    assert liml.elevation_m == pytest.approx(200.0)      # overridden
    assert liml.name == "Milano Linate"                  # unchanged
    assert liml.lat_deg == pytest.approx(45.4451)        # unchanged
    assert liml.lon_deg == pytest.approx(9.2767)
    assert liml.runway_hdg_deg == pytest.approx(176.0)
    # and no other row moved
    assert airports.find(loaded, "KSFO").elevation_m == pytest.approx(4.0)
    assert len(loaded) == 4


def test_override_can_change_every_field(tmp_path):
    """P-01.04-2. A full override of a known ICAO replaces every stated key."""
    path = write_override(tmp_path, {"EDDF": {
        "name": "Frankfurt", "lat_deg": 50.0, "lon_deg": 8.0,
        "runway_hdg_deg": 70.0, "elevation_m": 100.0,
    }})
    eddf = airports.find(airports.load_airports(override_path=path), "EDDF")
    assert (eddf.name, eddf.lat_deg, eddf.lon_deg, eddf.runway_hdg_deg,
            eddf.elevation_m) == ("Frankfurt", 50.0, 8.0, 70.0, 100.0)


# ===========================================================================
# P-01.04-3
# ===========================================================================
@pytest.mark.parametrize("text", [
    "{not json",
    "",
    "\x00\x01\x02",
    "[1, 2, 3]",          # valid JSON, wrong shape
    '"a string"',
])
def test_corrupt_override_file_is_tolerated(tmp_path, text):
    """P-01.04-3. [a: D13] "Tolerated without failing the load: a corrupt
    file". The seed table comes back and nothing is raised.
    """
    path = write_override(tmp_path, text)
    loaded = airports.load_airports(override_path=path)
    assert len(loaded) == 4
    assert airports.find(loaded, "LIML").elevation_m == pytest.approx(107.6)


def test_absent_override_file_is_tolerated(tmp_path):
    """P-01.04-3. An absent file is the normal case, not an error. [a: D13]"""
    loaded = airports.load_airports(directory=str(tmp_path))
    assert len(loaded) == 4


# ===========================================================================
# P-01.04-4
# ===========================================================================
@pytest.mark.parametrize("bad", ["north", None, True, [1], {"a": 1},
                                 float("nan"), 1e400])
def test_corrupt_value_in_a_valid_file_is_tolerated(tmp_path, bad):
    """P-01.04-4. [a: D13] "a corrupt value inside a valid file" -- the key is
    ignored, the rest of the row still applies, nothing is raised.
    """
    payload = json.dumps({"LIML": {"lat_deg": bad, "elevation_m": 500.0}})
    p = tmp_path / airports.OVERRIDE_FILENAME
    p.write_text(payload, encoding="utf-8")
    liml = airports.find(airports.load_airports(override_path=str(p)), "LIML")
    assert liml.lat_deg == pytest.approx(45.4451)        # corrupt key ignored
    assert liml.elevation_m == pytest.approx(500.0)      # rest of row applied


def test_out_of_range_coordinate_is_corrupt(tmp_path):
    """P-01.04-4. A latitude of 400 degrees is a corrupt value, not a distant
    airport. [b: D13]
    """
    path = write_override(tmp_path, {"LIML": {"lat_deg": 400.0}})
    liml = airports.find(airports.load_airports(override_path=path), "LIML")
    assert liml.lat_deg == pytest.approx(45.4451)


# ===========================================================================
# P-01.04-5
# ===========================================================================
def test_unknown_icao_partial_ignored_complete_added(tmp_path):
    """P-01.04-5. [a: D13] "a partial row for an unknown ICAO (ignored). A
    complete row for an unknown ICAO is added."
    """
    path = write_override(tmp_path, {
        "ZZZZ": {"elevation_m": 10.0},                       # partial, unknown
        "EGLL": {                                            # complete, unknown
            "name": "London Heathrow", "lat_deg": 51.4700,
            "lon_deg": -0.4543, "runway_hdg_deg": 270.0,
            "elevation_m": 25.0,
        },
    })
    loaded = airports.load_airports(override_path=path)
    assert airports.find(loaded, "ZZZZ") is None
    egll = airports.find(loaded, "EGLL")
    assert egll is not None
    assert egll.name == "London Heathrow"
    assert egll.lat_deg == pytest.approx(51.47)
    assert len(loaded) == 5
    # the seed rows are untouched and still first, in D13's order
    assert [a.icao for a in loaded[:4]] == ["LIML", "LIMC", "KSFO", "EDDF"]


def test_unknown_icao_with_one_missing_key_is_still_partial(tmp_path):
    """P-01.04-5. "Complete" means every column of the D13 table. [b: D13]"""
    path = write_override(tmp_path, {"EGLL": {
        "name": "London Heathrow", "lat_deg": 51.47, "lon_deg": -0.4543,
        "runway_hdg_deg": 270.0,        # elevation_m missing
    }})
    assert airports.find(airports.load_airports(override_path=path),
                         "EGLL") is None


def test_unknown_icao_with_a_corrupt_key_is_partial(tmp_path):
    """P-01.04-5 x P-01.04-4. A corrupt value makes an otherwise complete row
    incomplete, so it is ignored rather than added half-formed. [b: D13]
    """
    path = write_override(tmp_path, {"EGLL": {
        "name": "London Heathrow", "lat_deg": "fifty-one", "lon_deg": -0.4543,
        "runway_hdg_deg": 270.0, "elevation_m": 25.0,
    }})
    assert airports.find(airports.load_airports(override_path=path),
                         "EGLL") is None


def test_icao_keys_are_case_insensitive(tmp_path):
    """[b: D13] a hand-edited file is likely to use lower case."""
    path = write_override(tmp_path, {"liml": {"elevation_m": 300.0}})
    liml = airports.find(airports.load_airports(override_path=path), "LIML")
    assert liml.elevation_m == pytest.approx(300.0)
    assert len(airports.load_airports(override_path=path)) == 4


def test_combo_label_names_the_airport_and_its_icao():
    """[b: D14, reference screenshot] the Mission Control combo reads
    "Munich (EDDM)" -- name then ICAO in parentheses.
    """
    assert Airport("LIML", "Milano Linate", 0, 0, 0, 0).label() == (
        "Milano Linate (LIML)"
    )
