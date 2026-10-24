# Closure audit output — R-10

Produced by `tools/closure_audit.py` with no arguments, on 2026-09-08.
Reproduce with:

```
python tools/closure_audit.py                  # this output
python tools/closure_audit.py --demo-fixture   # the fixture demonstration
python tools/closure_audit.py --strict-manual  # gate G8
```

The audit's evidence is the source files, the workbook and the plan's own rows. It reads
`spec/fsim_Design_v5.xlsx` with the standard library alone (`zipfile` + `xml.etree`), so it
runs in `.venv_fsim` as §5.2 defines it — see `docs/MODEL_INTAKE.md` F-1.

---

## Output

```
==========================================================================
 CLOSURE AUDIT -- R-10
==========================================================================
 workbook          spec/fsim_Design_v5.xlsx
 L2 requirements   34  (27 carry T, 9 carry I or D)

-- Check 1: cited ids exist in the workbook
   EMPTY  (every cited requirement id is a real workbook row)

-- Check 2: every workbook T row is cited by a test
   EMPTY  (all 27 T rows are cited)

-- Check 3a: every implemented test maps to a plan row
   EMPTY  (no test exists outside the plan, its amendments or the register)

-- Check 3b: no frozen plan row removed without an amendment
   EMPTY  (every named plan row is implemented)

-- Check 4: every port constant matches the TA
   EMPTY  (ports.py, D4 and the TA files agree)

-- Check 5: every I/D row has an attributed PASS
   9 ITEM(S)  (all 9 I/D rows are recorded)
     - L2SYS-REQ-01.01: OPEN
     - L2SYS-REQ-01.03: OPEN
     - L2SYS-REQ-01.03b: OPEN
     - L2SYS-REQ-01.05: OPEN
     - L2SYS-REQ-01.05b: OPEN
     - L2SYS-REQ-01.08c: OPEN
     - L2SYS-REQ-02.01: OPEN
     - L2SYS-REQ-05.01: OPEN
     - L2SYS-REQ-05.02: OPEN

-- Check 6: every flightsim/ module carries [a]/[b]/[c]
   EMPTY  (all 22 modules are annotated)

-- Provenance census (check 6)
   [a] 609   [b] 49   [c] 20   -> docs/PROVENANCE.md
   93 distinct citation targets; 20 [c] occurrences inventoried
   Counts are tag occurrences and the classification is self-reported.

-- Test count
   test functions   273
   pytest items     366   (parametrized rows expand)

==========================================================================
 RESULT: checks 1-4 and 6 complete; check 5 reports 9 OPEN manual row(s).
         Run with --strict-manual to make those a failure.
==========================================================================
```

---

## Reading it

**Checks 1, 2, 3a, 3b, 4 and 6 are empty or complete.** That is gate **G5**, and check 4
together with reading `flightsim/common/ports.py` is gate **G6**. Check 4 does not trust a
transcription: it re-derives each port from `model/*.sysml` at audit time and compares it
against both the constant in `ports.py` and the literal from D4.

**Check 5 reports all nine `I`/`D` requirements as OPEN**, which is its default behaviour and
is the correct result for this run: R-11 and R-12 are the practitioner's, on the Windows
target, and no manual item has been performed. `--strict-manual` therefore exits non-zero,
so gate **G8 is not passed and is not claimed**. See `docs/MANUAL_RESULTS.md`.

**Two defects in the audit itself were found and fixed while it was being written**, both by
the fixture demonstration R-10 requires:

1. The audit credited a `PASS` row that sits inside a fenced code block in
   `docs/MANUAL_RESULTS.md` and is labelled "for format only — this is **not** a result".
   That silently discharged `L2SYS-REQ-01.01`: the first run reported **8** OPEN rows where
   there are **9**. A format example must never discharge a requirement, so the audit now
   skips fenced blocks, and `--demo-fixture` asserts exactly this case.
2. Check 3b reported every test *module* (`test_pcs.py` and the rest) as a frozen plan row
   that had been removed, because the plan writes rows as `module.py::name` and the name
   pattern matched the module half. Module references are now stripped before the match.

The test count is reported two ways because they differ: **273 test functions** collect as
**366 pytest items**, the difference being parametrized rows. Neither number is a target;
both are outcomes of the plan.

## Provenance census

`docs/PROVENANCE.md` carries the full census. Its headline: **609 `[a]`, 49 `[b]`, 20 `[c]`**
across 22 modules, with 93 distinct citation targets and every one of the 20 `[c]`
occurrences inventoried by file and line. Those counts are **tag occurrences**, not lines or
decisions, and the classification is self-reported by whoever wrote each line — a module with
many `[a]` tags is not thereby more correct than one with few. What the census is good for is
the `[c]` inventory: it is the complete list of places this package made a choice no supplied
artifact settles, each carrying, at its line, what would settle it.
