# R-10 — Closure Audit

Mechanically produced by `tools/closure_audit.py` (run it yourself with
`python tools/closure_audit.py`, or `.venv_fsim\Scripts\python.exe
tools\closure_audit.py` on the target platform). The workbook it audits
against is the shipped copy in `spec/fsim_Design_v4.xlsx`.

## What R-10 asks for

1. every `L2SYS-REQ-*` cited anywhere in `flightsim/` or `tests/` exists
   in the workbook;
2. every workbook requirement with V-Method `T` is cited by at least one
   test docstring;
3. every implemented test maps back to a verification-plan row
   (plan ↔ suite consistency, amendments included);
4. every port constant matches the TA.

All four lists come back empty / complete. The final test count is
reported as an **outcome**, not checked against a target.

## Suite status

```
$ QT_QPA_PLATFORM=offscreen python -m pytest tests -q
241 passed
```

Run twice back to back with the same result (R-9 acceptance: no
cross-test leakage). `pytest` counts 241 because several plan rows are
implemented as parametrized tests; the audit below counts 208 test
*functions* across 9 modules.

Includes the rows added by plan amendments **A-6** (FlightGear install
location moved to the environment, plus the Qt signal-argument defect
found while fixing it) **A-7** (deployment rows made hermetic; launch severity split into
blocking vs. warning) **A-8** (`--fg-aircraft` semantics verified and diagnosed at runtime)
and **A-9** (`fsim_env.bat` read directly as a config file).

Verified green under all three machine conditions — **with** a FlightGear
installation present, **without** one, and with a real `fsim_env.bat`
sitting in the repository root (A-7 and A-9 hermeticity).

## Audit output

```
==========================================================================
R-10 CLOSURE AUDIT
==========================================================================
workbook           : spec/fsim_Design_v4.xlsx
L2 requirements    : 34
  V-Method T       : 27
  V-Method I       : 7
  V-Method D       : 2

(1) DANGLING REQUIREMENT CITATIONS (every L2SYS-REQ-* cited in flightsim/ or tests/ exists in the workbook)
    [] empty - no dangling citations

(2) UNCOVERED `T` REQUIREMENTS (every workbook T row cited by >= 1 test docstring)
    [] empty - all 27 T requirements covered

(3) PLAN <-> SUITE INCONSISTENCIES (every test maps to a plan row; every plan row is implemented)
    [] empty - plan and suite are consistent

(4) PORT-CONSTANT MISMATCHES vs the SysML v2 TA
    [] empty - all 8 endpoints match the TA

--------------------------------------------------------------------------
REQUIREMENT COVERAGE (workbook order)
--------------------------------------------------------------------------
  L2SYS-REQ-01.01      I     manual (docs/MANUAL_VERIFICATION.md)
  L2SYS-REQ-01.01b     T     7 test(s)
  L2SYS-REQ-01.02      T     1 test(s)
  L2SYS-REQ-01.02b     T     5 test(s)
  L2SYS-REQ-01.03      I     manual (docs/MANUAL_VERIFICATION.md)
  L2SYS-REQ-01.03b     I     manual (docs/MANUAL_VERIFICATION.md)
  L2SYS-REQ-01.04      T     3 test(s)
  L2SYS-REQ-01.04b     T     1 test(s)
  L2SYS-REQ-01.05      I     manual (docs/MANUAL_VERIFICATION.md)
  L2SYS-REQ-01.05b     I     manual (docs/MANUAL_VERIFICATION.md)
  L2SYS-REQ-01.06      T     1 test(s)
  L2SYS-REQ-01.06b     T     4 test(s)
  L2SYS-REQ-01.06c     T     2 test(s)
  L2SYS-REQ-01.06d     T     2 test(s)
  L2SYS-REQ-01.06e     T     3 test(s)
  L2SYS-REQ-01.07      T     2 test(s)
  L2SYS-REQ-01.08      T     1 test(s)
  L2SYS-REQ-01.08b     T     3 test(s)
  L2SYS-REQ-01.08c     I,T   2 test(s)
  L2SYS-REQ-01.10      T     1 test(s)
  L2SYS-REQ-01.10b     T     29 test(s)
  L2SYS-REQ-01.11      T     3 test(s)
  L2SYS-REQ-02.01      I,T   8 test(s)
  L2SYS-REQ-02.03      T     4 test(s)
  L2SYS-REQ-02.03b     T     7 test(s)
  L2SYS-REQ-02.04      T     4 test(s)
  L2SYS-REQ-03.01      T     3 test(s)
  L2SYS-REQ-03.02      T     7 test(s)
  L2SYS-REQ-03.03      T     4 test(s)
  L2SYS-REQ-03.04      T     3 test(s)
  L2SYS-REQ-03.05      T     11 test(s)
  L2SYS-REQ-04.01      T     10 test(s)
  L2SYS-REQ-05.01      D     manual (docs/MANUAL_VERIFICATION.md)
  L2SYS-REQ-05.02      D     manual (docs/MANUAL_VERIFICATION.md) + 1 incidental test(s)

--------------------------------------------------------------------------
FINAL TEST COUNT (an OUTCOME, not a target): 208 tests in 9 modules
    tests/test_config.py               27
    tests/test_end_to_end.py            6
    tests/test_hotas.py                 8
    tests/test_interfaces.py           27
    tests/test_map_window.py           14
    tests/test_orchestrator.py         12
    tests/test_pcs.py                  59
    tests/test_physics.py              29
    tests/test_sem.py                  26
--------------------------------------------------------------------------
AUDIT RESULT: PASS
```

## Notes

- The `V-Method I : 7` line counts requirements whose method **includes**
  `I`; two of them (01.08c, 02.01) are `T, I`, so the disjoint tally is
  27 `T` / 5 `I`-only / 2 `D`, i.e. 34 L2 requirements.
- `L2SYS-REQ-05.02` is a `D` (demonstration) row and is therefore verified
  manually (M-9). It nevertheless shows incidental automated tests,
  because the tile-endpoint rows also assert the OpenStreetMap endpoint.
  That is coverage in addition to the required manual demonstration, not a
  substitution for it.
- Amendments A-1 … A-9 of `docs/VERIFICATION_PLAN.md` are part of the plan
  for the purposes of check (3): since A-6, check (3) recognises an
  amendment heading as a plan row in its own right, which is what R-10
  means by "plan ↔ suite consistency, amendments included".
