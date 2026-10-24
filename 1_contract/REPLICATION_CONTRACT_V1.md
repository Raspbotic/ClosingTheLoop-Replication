# Reviewer Recreation Protocol
## Executable Prompt Set: SysML v2 Model + Requirements → Flight Simulator + Regression Suite

**Purpose.** Unlike the historical prompt log (`replication_prompts.md`), this is a
*forward-engineering* protocol: a self-contained prompt sequence a reviewer can
issue to an LLM with a code-execution sandbox to recreate a functionally
equivalent application and its regression test batch from the specification
artifacts. The original development converged through ~59 interactive turns,
many of them hardware-in-the-loop; here, the decisions that emerged from those
iterations are consolidated into a **Decision Annex (Section 2)** supplied up
front, so the recreation needs no target hardware to reach code-complete state.

**What "successful recreation" means.** LLM output is not bit-reproducible.
The acceptance criteria are the invariants, checked after R-10:
(i) process/port architecture identical to the SysML TA;
(ii) every code module carries [a]/[b]/[c] traceability annotations citing
requirement IDs that exist in `fsim_Design_v1_final.xlsx` (zero dangling
citations, verified mechanically);
(iii) a pytest regression suite, derived from the requirements and their
selected verification methods (R-2) BEFORE implementation, all passing
headless, with requirement IDs in test docstrings such that every requirement
marked `T` in the workbook's V-Method column is covered by at least one test
that verifies its statement (the test COUNT is an outcome, not a target; the
original development converged to 56);
(iv) the wire protocol of Section 2 (so independently recreated processes could
interoperate).

---

## 1. Inputs to attach before R-0

1. The 17 SysML v2 files (final revisions: BusOrchestrator naming, PythonRuntime
   attributes, SSF doc comments incl. `ssfSelectAirport`, SEM `calcRadarEquation`).
2. `fsim_Design_v1_final.xlsx` (34 L2 requirements, zero-padded IDs, base/b
   convention, R2F matrices, V-Method column T/I/D).
3. Reference GUI screenshots (`Desired_UIUX_capture.JPG`,
   `Desired_FGear_capture.JPG`) — a requirements-level UI reference invoked by
   the panel requirements (01.06/01.08x/01.10x/01.11) and D14; NOT an
   architecture source. The SysML v2 files are the sole LA/TA source; the
   shipped revisions are the corrected, self-consistent ones (the historical
   SEM port erratum is fixed in the model itself).
4. **Legacy HOTAS code**: the prior-project `hotas_standalone.py` (axis mapping:
   roll 0, pitch 1, throttle 2, yaw candidates 3/4; debug printout style) and the
   legacy `launch_flightgear()` function (FG command line).
5. The Decision Annex (Section 2 of this document) as a context file.

## 2. Decision Annex (customer decisions not derivable from the model)

Supply verbatim. Tag in generated code as `[a: Decision Dn]`.

- **D1 Wire format.** JSON-over-UDP dataclasses: `HotasControls` (seq, roll/
  pitch/yaw ∈ [−1,1], throttle ∈ [0,1], switches dict), `OwnshipState`,
  `RadarBeacon` (beacon_id, classification FRIEND/FOE/UNKNOWN, lat/lon/alt,
  max_range_m, detected, slant_range_m, active). Malformed datagrams rejected
  with a dedicated error type.
- **D2 Beacon protocol.** Event-driven: transmit only the changed beacon;
  deletion via tombstone (`active=False`); background refresh every 2.0 s;
  receiver garbage-collects beacons stale > 5.0 s (stale ≥ 2 × refresh).
- **D3 Orchestrator.** Self-contained Python `selectors` UDP relay (no external
  middleware), two internal nodes per the TA (seNode, pcsNode), payload-opaque,
  non-blocking; clean OSError-tolerant shutdown (Windows teardown race).
- **D4 Port map** (authoritative, as specified across the TA files): HOTAS→PCS 5005;
  PCS→orch 5006; orch→SEM 5007; SEM→orch 6007; orch→PCS 6006; PCS→FlightGear
  native-fdm 5550 (FGNetFDM version 24, 408 bytes, big-endian, decimated to
  ~60 Hz); FG httpd 8080, ground elevation at `/json/position/ground-elev-m`
  (value may arrive as string); OSM tiles `https://tile.openstreetmap.org`.
- **D5 Runtimes.** Two venvs per the TA PythonRuntime attributes: `.venv_fsim`
  (Python 3.14: PCS, orchestrator, SEM) and `.venv_hotas` (Python 3.11: pygame
  has no cp314 wheels). Launch script activates per-window; never a global
  activate. All tooling invokes venv interpreters by explicit path.
- **D6 Aircraft & OTW.** JSBSim model `f16`; FlightGear visual `YF-16` from a
  user aircraft dir; FG launch command exactly as the legacy function:
  `fgfs --fdm=null --native-fdm=socket,in,60,,5550,udp --fg-aircraft=<dir>
  --aircraft=YF-16 --airport=<ICAO> --timeofday=noon --httpd=8080
  --prop:/input/joysticks/js[0]=0 --prop:/input/joysticks/js[1]=0`.
- **D7 Control mapping.** btn2 = landing-gear toggle; btn4 = air-brake toggle;
  btn6 = momentary wheel brake; yaw drives BOTH `fcs/rudder-cmd-norm` and
  `fcs/steer-cmd-norm` (nose-wheel steering). HOTAS yaw axis: pick the larger
  deflection of candidate axes 3/4 ("smart yaw", per legacy hardware).
- **D8 Ground/engine semantics** (realizing 01.04/01.06b/01.07): spawn on
  runway with engine OFF (JSBSim `propulsion/cutoff_cmd` — underscore, the
  hyphenated name fails silently), PRK BRK ON, gear down; engine start refused
  above 10% throttle (idle gate); start = cutoff 0 + `set-running=-1`; SSF
  set-position airborne implies engine ON and parking brake released; reset =
  clean slate (AP/fuel-freeze disengaged) and publishes one telemetry + one
  FG frame even while flight-frozen.
- **D9 Terrain loop.** Poll FG ground elevation at 2 Hz; feed into JSBSim
  `position/terrain-elevation-asl-ft` slewed ≤ 1 ft per 120 Hz frame.
- **D10 Virtual HOTAS** (01.08b): physical precedence window 1.0 s (fresh 5005
  datagram wins; widget greys out with title change); stick and pedals spring
  to neutral on ANY mouse release (override mouseReleaseEvent — the
  sliderReleased signal misses groove clicks); on physical loss, stick/pedals
  center while throttle retains last position (bumpless); widget mirrors live
  physical commands at 10 Hz while locked; throttle slider painted with scale
  IDLE 0 / 25 / 50 / 75 / MIL 100 and AB marker at top of travel; drag-down =
  stick back = pitch +1 (matches physical sign).
- **D11 Autopilot** (01.11): engaging AP/AH/AT captures the current value as
  target (sync-on-engage). AH is an attitude cascade: vs_cmd = clip(0.15·alt_err,
  ±5 m/s); theta_cmd = clip(1.5·(vs_cmd − vs), −8°, +12°); pitch =
  clip(0.06·(theta_cmd − pitch_deg), ±1). AP: bank_cmd = clip(0.8·hdg_err, ±25°),
  roll = clip(0.03·(bank_cmd − roll_deg)). AT: throttle = clip(0.5 + 0.04·spd_err).
- **D12 Radar equation** (02.02→03.05 chain, computed in SEM): monostatic
  R_max = (Pt·G²·λ²·σ / ((4π)³·P_min))^¼; published in the existing
  `max_range_m` field (wire unchanged); detection = slant ≤ R_max; per-beacon
  editable Pt/G/f/P_min (defaults 100 kW / 35 dBi / 10 GHz / −90 dBm), ownship
  RCS as scenario spinbox (default 5 m² → ≈ 39 km); an RCS change recomputes
  and retransmits every beacon; beacons without emitter params keep their
  explicit range (test compatibility).
- **D13 Airports.** Seed table LIML, LIMC, KSFO, EDDF (ICAO, name, lat/lon,
  runway heading, elevation); optional `airports_local.json` per-ICAO partial
  overrides, corrupt file survivable.
- **D14 PCS layout** (per reference screenshot): attention-getter strip (empty
  placeholder; 02.04 PULL UP is spec'd but deferred), tabulated Ownship State
  readouts (fixed-width, right-aligned), Virtual HOTAS left, System Controls
  (state-colored PRK BRK / ENGINE / AIR BRAKE / GEAR), Navigation Controls
  (Alt/Spd/Hdg + AH/AT/AP), Mission Control (airport combo, freeze-gated),
  Sim Control SSF button grid (RUN/PAUSE/RESET/LAUNCH 3D OTW/SET POSITION/
  Save/Load Snapshot/FUEL FREEZE/Telemetry Capture), message log box; dark QSS.
  Telemetry CSV header: the 28 fields verbatim from the `ssfLogTelemetry` doc.
- **D15 Test conventions.** pytest; offscreen Qt (`QT_QPA_PLATFORM=offscreen`);
  tests bind the real UDP ports (document the "close running instances" caveat);
  long-running end-to-end and autopilot tests marked `slow`; every spawned
  thread stoppable (stop_event) so no test leaks input into later tests;
  requirement IDs cited in test docstrings; runner script invokes the venv
  python by path and pauses on exit.

## 3. Prompt sequence

Issue in order; each prompt assumes the previous deliverables are in the sandbox.
*Italic acceptance lines* are checked by the reviewer (or by asking the LLM to
demonstrate them) before proceeding.

**R-0 — Role and ground rules** `[adapted from the original system prompt]`
> You are the SW developer of this project. Objective: develop a SW application
> that complies with the attached design specification (SysML v2 models +
> requirements workbook). Rules: (1) all generated code shall be traceable to
> specification items — annotate with [a] = backed by the specification or the
> Decision Annex (cite the ID), [b] = logically derived, [c] = best guess or topic open to author's choice
> (flag inline); (2) analyse each prompt for token feasibility and propose a
> step breakdown if needed; (3) verify your own outputs by executing them in
> your sandbox before delivering; (4) deliver files, not snippets. Target
> platform: Windows gamer laptop, Python 3.14 + 3.11, physical HOTAS,
> FlightGear installed. Acknowledge and summarize the specification inventory.

**R-1 — Architecture**
> Inspect the specification. Derive the process architecture (OS processes,
> threads per the TA thread decomposition, all inter-process links with port
> numbers). Cross-check every port for INTERNAL consistency across the TA
> files (each fsim_TA_Orch.sysml node port must pair with the corresponding
> boundary port of the connected subsystem's TA file, and with D4); report any
> inconsistency instead of silently choosing. Deliver an architecture memo with
> a link table (source, sink, port, protocol, rate) and the planned file tree
> (package `flightsim/`, subpackage `pcs/`, `tests/` beside it).
> *Acceptance: link table identical to Decision D4; six processes (HOTAS, PCS,
> orchestrator, SEM, FlightGear, OSM) with PCS threads FlightSim/Physics/
> MapWindow per the TA.*

**R-2 — Verification plan (derive the test suite from the requirements)**
> From the workbook, extract every L2 requirement and its V-Method. For each
> requirement marked `T`, derive a TEST SPECIFICATION from the requirement
> statement alone: stimulus, observation point, pass criterion, and any
> reference values computed INDEPENDENTLY of the future implementation (e.g. a
> requirement stating a formula is verified against a hand computation of that
> formula; a requirement stating a rate is verified by measuring that rate).
> Add conformance checks for the interface decisions D1–D4 (wire round-trips,
> tombstone semantics, port constants, packet layout) — these are
> specification items and get [a: Dn] citations. For rows marked `I` or `D`,
> produce instead a one-line manual verification instruction (these are OUT of
> the automated suite by design). Deliver the plan as a table: requirement ID,
> V-Method, test name(s), pass criterion. This plan is FROZEN before
> implementation; later prompts implement it module by module. If, during
> implementation, a test proves infeasible as specified, amend the plan
> explicitly and justify — never silently.
> *Acceptance: every `T` row has ≥ 1 test specification; no specification
> references implementation internals that the requirements do not name.*

**R-3 — Common layer**
> Implement `flightsim/common/ports.py` (all constants of D4, each cited to its
> TA source) and `flightsim/common/messages.py` (D1 dataclasses, JSON
> encode/decode, D2 beacon-protocol constants). Then create `tests/` with
> conftest (offscreen Qt, sys.path, a UDP-receive fixture) and a first test
> module asserting: every port constant against D4; message round-trips;
> malformed rejection; tombstone semantics; stale ≥ 2×refresh — i.e. the
> D1–D4 conformance rows of the verification plan.
> *Acceptance: suite runs green; port tests would fail if any constant changed.*

**R-4 — HOTAS transmitter (legacy code attached)**
> Implement `flightsim/hotas_standalone.py` against L2SYS-REQ-01.01b and the
> HotasControl actions in fsim_TA_Peripherals.sysml, using the attached legacy
> file as the authority for axis mapping and the debug-printout style (D7 smart
> yaw). Include: `--demo` (hardware-free synthetic source), `--debug`,
> `--debug-axes`, `--list`; 60 Hz transmit loop with optional stop_event (D15);
> hot-plug tolerance — start without a device and wait, suspend transmission on
> JOYDEVICEREMOVED (so the PCS falls back to virtual controls), resume on
> reconnect with a 1 Hz joystick-subsystem rescan fallback. Implement the verification-plan tests for the requirements
> allocated to this module (01.01b and the D7 yaw-selection conformance
> check), honoring D15 (any transmit thread stoppable in teardown).
> *Acceptance: applicable plan rows green; no port 5005 traffic after
> teardown.*

**R-5 — Physics thread**
> Implement `flightsim/pcs/physics.py` (ThreadPhysics per
> fsim_TA_PCS_ThreadPhysics.sysml: init phase + 120 Hz cyclic loop) and
> `flightsim/pcs/airports.py` (D13). Realize: JSBSim `f16`; D8 ground/engine
> semantics; D7 button map incl. dual rudder/steer routing; D9 terrain
> feedback setter; FGNetFDM v24 packing (408 bytes, big-endian) decimated to
> 60 Hz on port 5550; SSF backend methods (reset, set position, snapshot state,
> fuel freeze). Cite 01.02b, 01.04, 01.06b–e, 01.07 at the implementing lines.
> Implement the verification-plan tests for the requirements allocated to this
> module (01.02/01.02b, 01.04, 01.07, plus the D6–D9 conformance rows); a
> shared module-scoped ground-start fixture is advisable (JSBSim init is slow).
> *Acceptance: applicable plan rows green on the reviewer's machine.*

**R-6 — Bus orchestrator**
> Implement `flightsim/master_node.py` (BusOrchestrator per fsim_TA_Orch.sysml,
> D3): seNode and pcsNode with the TA port bindings, selectors loop, opaque
> forwarding, stop() safe on Windows. Implement the verification-plan tests
> for 04.01 (both relay paths, tombstone transit, non-blocking behavior under
> a datagram burst).
> *Acceptance: applicable plan rows green, no unhandled-thread-exception
> warnings.*

**R-7 — SEM and tactical map**
> Implement `flightsim/run_sem.py` (SyntheticEnvironmentMgr per
> fsim_TA_SEM.sysml: connect status, beacon inject/edit/remove GUI with D12
> emitter parameters and live computed R_max, ownship RCS control, slant-range
> calc, event-driven transmission per D2, calcRadarEquation per D12 citing
> 03.05) and `flightsim/pcs/map_window.py` (ThreadMapWindow per its TA file:
> MapGuiWidget + TileFetcher QThread, slippy-map math, disk tile cache, ownship
> symbol, beacon threat rings color-coded by classification citing 02.03b).
> Implement the verification-plan tests for the requirements allocated here
> (02.01, 02.03b, 03.01–03.05, plus the D2/D12 conformance rows). The plan's
> independence rule applies especially to 03.05: reference values must be
> computed in the test from the formula stated in the requirement, never
> copied from the implementation.
> *Acceptance: applicable plan rows green.*

**R-8 — Pilot Control Station**
> Implement `flightsim/pcs/main_window.py` and `flightsim/pcs/virtual_hotas.py`:
> the full D14 layout against the reference screenshot; IOManager bridging
> UDP↔physics queues with D10 physical-precedence and virtual fallback;
> autopilot per D11; SSF menu + button grid sharing one freeze gate (buttons
> trigger the same actions); snapshot save/load (no aliasing); telemetry CSV
> logger (28-field header verbatim); FlightGear launch per D6; terrain loop
> wiring per D9. Cite 01.06, 01.06b–e, 01.08, 01.08b, 01.10, 01.10b, 01.11,
> 02.03 at the implementing panels. Implement the verification-plan tests for the
> requirements allocated here (01.04b, 01.06/01.06b–e via the GUI path,
> 01.08/01.08b, 01.10/01.10b, 01.11, 02.03, plus the D10/D11 conformance
> rows). Where a requirement names a UI element, the test drives the real
> widget offscreen, not an internal API.
> *Acceptance: applicable plan rows green.*

**R-9 — Integration deliverables**
> Deliver `launch_flightsim.bat` (per-window venv activation per D5; FG block
> commented with the D6 command line), `run_tests.bat` (D15: venv python by
> path, works from root or tests\, pauses, forwards pytest args),
> `requirements_main.txt`, `requirements_hotas.txt`, and an end-to-end test:
> orchestrator + PCS + SEM + demo HOTAS in one process space over the real
> ports — assert ownship reaches SEM, demo controls reach the PCS, an injected
> beacon reaches PCS and map with correct detection, a tombstone clears both
> (marked slow) — realizing the plan rows that require end-to-end observation
> (02.03 reception across the real bus).
> *Acceptance: full suite green headless; suite re-run twice in a row stays
> green (no cross-test leakage).*

**R-10 — Closure audit**
> Mechanically audit: (1) every `L2SYS-REQ-*` cited anywhere in flightsim/ or
> tests/ exists in the workbook; (2) every workbook requirement with V-Method
> `T` is cited by at least one test docstring; (3) every implemented test maps
> back to a verification-plan row (plan↔suite consistency, amendments
> included); (4) every port constant matches the TA. Output the four lists
> (should all be empty/complete) and the final test count — reported as an
> outcome, not checked against a target.
> *Acceptance: zero dangling citations; full T-coverage; plan and suite
> consistent; suite green.*

**R-11 — Optional hardware verification (requires target hardware)**
Checklist, not prompts: physical HOTAS taxi (nose-wheel steering, btn2/4/6),
three hot-plug sequences (start-unplugged, unplug-in-flight → virtual takeover
≤ ~1 s, replug → physical reclaims), FlightGear launch from the SSF button and
terrain agreement while taxiing, OSM live tiles, AH engage damping by feel.
These correspond to workbook rows marked I/D and are outside the automated
acceptance.

---

## 4. Relation to the original development

The original run reached the same end state through ~59 interactive turns in
which roughly a third of the Decision Annex was *discovered* (HIL defect rounds
P-54…P-56 of `replication_prompts.md` produced D7's steer routing, D9, D10's
spring/mirror/fallback details, D11's cascade gains). This protocol front-loads
those findings; a reviewer wishing to study the *interactive* method instead
should follow the historical log and expect to supply their own hardware
observations at the marked [HIL] points.

---

## Appendix — Realized verification plan of the original development (outcome, not input)

For comparison with the reviewer's independently derived R-2 plan; a reviewer's
plan need not match these designs — it must verify the same requirement
statements. The original suite converged to 56 tests in 8 modules.
Representative realizations: 01.02b → real-time-factor measurement 0.85–1.15
over 2 s; 01.07 → fuel-mass invariance with engine OFF, then spool + burn
after an idle-gated start; 03.05 → hand-computed R_max reference plus
fourth-root scaling laws (16× RCS → 2× range, +12 dB sensitivity → ½ range);
02.03b → offscreen render of a FOE beacon with a pixel-level red-ring check;
01.08b → physical-precedence round-trip (datagram claims control and greys the
widget; timeout falls back with stick centered, throttle retained); 01.10b
RESET → dirty-everything-then-reset asserting engine OFF, PRK BRK ON, modes
disengaged, position published while paused; D2 → tombstone transit and the
stale ≥ 2×refresh invariant; D4 → one assertion per port constant.
