# Replication Contract — Version 4
## Prompt Set: SysML v2 Model (v5 baseline) + Requirements Workbook → Flight Simulator + Regression Suite

### Purpose

This document is a prompt sequence. A reviewer issues it to an LLM with a
code-execution sandbox. The LLM recreates a functionally equivalent application and
its regression suite from the specification artifacts, with minimal expert
intervention.

Rules of authority:

* The model decides structure, interfaces and values. Processes, threads, parts,
  ports, message types, field names, rates, ranges and runtime assignments come from
  the SysML v2 files and the workbook. No prompt in this document states a module
  count, a file tree, a port number or a value the model carries.
* The Behaviour Annex (Section 2) decides behaviour. The model does not carry
  behaviours or sequences (no activity or sequence diagrams). The annex supplies them:
  what happens, in which order, and how the system reacts. Where the annex quotes a
  figure and the model states the same figure, the model's figure governs.

### Acceptance criteria

LLM output is not bit-reproducible. Acceptance is defined by the following
invariants, checked at R-10.

1. Process, thread and part architecture and every port are identical to the TA.
2. Every code module carries [a]/[b]/[c] traceability annotations. Every cited
   requirement ID exists in the workbook. Every cited model element exists in the
   model. Zero dangling citations, verified mechanically.
3. A pytest regression suite is derived from the requirements before implementation
   and passes headless. Requirement IDs appear in test docstrings. Every requirement
   with V-Method `T` is covered by at least one test that verifies its statement.
   The test count is an outcome, not a target.
4. The wire semantics of Section 2 are realized on the model's interface definitions.
5. The assembled application launches. Every TA part with a user interface is
   visible in it. This is checked by launching the application, not by a component
   test.

---

## 1. Inputs attached before R-0

1. **SysML v2 model.** 18 required files. A `.txt` transport suffix is transparent;
   the agent restores the `.sysml` name.
   Logical architecture and use cases: `fsim_LA.sysml`, `fsim_UCvsLA.sysml`.
   Technical architecture: `fsim_TA_Attributes.sysml`, `fsim_TA_Ports.sysml`,
   `fsim_TA_System.sysml`, `fsim_TA_PCS.sysml`, `fsim_TA_PCS_ThreadFlightSim.sysml`,
   `fsim_TA_PCS_ThreadFlightSim_UIM.sysml`, `fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml`,
   `fsim_TA_PCS_ThreadPhysics.sysml`, `fsim_TA_PCS_ThreadMapWindow.sysml`,
   `fsim_TA_Orch.sysml`, `fsim_TA_SEM.sysml`, `fsim_TA_Peripherals.sysml`.
   Requirements: `fsim_StakeholderRequirements.sysml`, `fsim_SystemRequirements.sysml`,
   `fsim_TA_IF_Requirements.sysml`, `fsim_R2F.sysml`.
   Optional: `fsim_Functions.sysml`. Conditional: `fsim_Addendum.sysml` (answered
   questions from a previous run; enters as model).
   The model is the sole LA/TA source and the authority for every interface value.
2. **Requirements workbook** `fsim_Design_v5.xlsx` or later. 34 L2 requirements,
   ID form `L2SYS-REQ-nn.nn` with `b`/`c` suffixes. R2F allocation to the logical
   blocks Hotas, VirtualOwnship, VisualSystem, PilotControlStation, Tactical2DMap,
   SyntheticEnvironment, BusManager. V-Method column at v5: 25 `T`, 2 `T, I`, 5 `I`,
   2 `D`; 27 rows carry `T`. The agent reports the counts it finds. A mismatch
   indicates a different workbook and is reported.
3. **Reference GUI screenshots** `Desired_UIUX_capture.JPG`,
   `Desired_FGear_capture.JPG`. Authoritative for the appearance and arrangement of
   the user interface, as images. The agent reproduces what the image shows
   (instruments where the image shows instruments, not labels). Not authoritative for
   data, behaviour, units or architecture.
4. **Legacy HOTAS code** `hotas_standalone.py` (axis mapping, debug printout style).
   Evidence of a previous implementation. Where it disagrees with the model, the model
   governs and the disagreement is reported.
5. **This document.** Section 2 is supplied as the behaviour context file.

**Precedence.** Structure, interfaces, values: the model, then the workbook. Behaviour
and sequences: Section 2, where the model is silent. Appearance: the screenshots, as
images. Any other conflict is reported, not resolved.

---

## 2. Behaviour Annex

Supply verbatim. Tag in generated code as `[a: Behaviour Bn]`. Each entry names the
model element it belongs to. A figure stated by the model overrides a figure stated
here.

- **B1 Wire semantics** (interface definitions in `fsim_TA_Ports.sysml` and
  `fsim_TA_IF_Requirements.sysml`). Messages are JSON datagrams over UDP, one message
  per datagram, with the fields and ranges the interface definitions declare:
  peripheral controls with sequence number and normalized axes; ownship state; radar
  beacon with ID, classification (FRIEND, FOE, UNKNOWN), position, maximum range,
  detection flag, slant range, active flag. A datagram that fails to decode or
  violates a declared range is rejected with an error type of this application. A
  receive loop is not terminated by a rejected datagram.
- **B2 Beacon protocol** (SEM to PCS beacon flow). Event-driven. A change transmits
  the changed beacon only. Deletion is a tombstone (`active=False`). A background
  refresh retransmits every live beacon periodically (2.0 s unless the model states
  a rate). The receiver removes a beacon not refreshed for two refresh periods.
- **B3 Orchestrator** (BusOrchestrator, `fsim_TA_Orch.sysml`). A standard-library UDP
  relay without external middleware. One internal node per TA node. Payload-opaque:
  relayed data is not decoded. Non-blocking, using `selectors`. Shutdown closes
  sockets and tolerates OSError on close (Windows teardown race).
- **B4 Visual-system launch** (SSF launch action). The command line is built in one
  place from named parts, following the legacy `launch_flightgear()` function: FDM
  disabled in the visual system; native-FDM socket input at the model's rate and
  port; user aircraft directory and aircraft identity from the model; airport from
  the current mission selection; time of day noon; HTTP server on the model's port;
  joystick inputs disabled. The executable is resolved from the environment
  (`FSIM_<TOOL>_*` variables), then PATH, then the platform's conventional install
  location. If unresolved, the launch control reports this and does nothing. The
  rest of the application runs without the visual system.
- **B5 Control mapping** (HotasControl actions, `fsim_TA_Peripherals.sysml`; PCS input
  handling). Two buttons toggle landing gear and air brake. One button is a momentary
  wheel brake. Assignment per the model; otherwise per the legacy file (btn2, btn4,
  btn6). Yaw drives both the rudder command and the nose-wheel steering command. On a
  device with two candidate yaw axes, the larger deflection is used (legacy
  hardware). Transmission suspends when the device is removed and resumes on
  reconnect; a periodic joystick-subsystem rescan is the fallback. The transmitter
  starts and waits when no device is present.
- **B6 Ground and engine sequence** (ThreadPhysics init and cyclic actions;
  01.04, 01.06b, 01.07). Initial state: on the runway threshold of the selected
  airport, engine OFF, parking brake ON, gear DOWN. Engine start is refused above the
  idle gate (10 % throttle). A start sets the engine running from cutoff. A
  set-position to an airborne state implies engine ON and parking brake released.
  RESET restores the initial ground state, disengages every autopilot mode and fuel
  freeze, and publishes one ownship telemetry message and one visual-system frame
  while the simulation is frozen. Fuel is invariant with the engine off and is
  consumed after a start.
- **B7 Terrain loop** (physics to visual system). The PCS polls the visual system's
  ground elevation over its HTTP endpoint at the model's rate (2 Hz if silent). The
  value may arrive as a string. The value is fed to the flight model's terrain
  elevation, slewed by at most one foot per physics frame. An absent endpoint (no
  visual system running) is tolerated without error.
- **B8 Virtual HOTAS precedence** (ThreadFlightSim UIM and BkgIOM; 01.08b). A fresh
  physical datagram claims control for a precedence window (1.0 s if the model is
  silent). During the window the on-screen controls are locked, greyed, retitled,
  and mirror the live physical commands at a visible rate. When the window expires
  the on-screen controls take over: stick and pedals centered, throttle at its last
  position. Stick and pedals return to neutral on any mouse release. Dragging the
  stick down is stick back, positive pitch (physical sign). The throttle carries the
  scale shown in the screenshot.
- **B9 Autopilot** (navigation controls; 01.11). Engaging altitude hold, heading hold
  or autothrottle captures the current value as target. Altitude hold is a cascade:
  vertical-speed command from altitude error (gain 0.15, clip ±5 m/s); pitch-attitude
  command from vertical-speed error (gain 1.5, clip −8° to +12°); pitch stick from
  attitude error (gain 0.06, clip ±1). Heading hold: bank command from heading error
  (gain 0.8, clip ±25°); roll stick from bank error (gain 0.03, clip ±1).
  Autothrottle: throttle = 0.5 + 0.04 × speed error, clipped to the throttle range.
  Gains stated by the model override these.
- **B10 Radar detection** (SEM `calcRadarEquation`; L2SYS-FCN-02.2, 03.05).
  Monostatic R_max = (Pt · G² · λ² · σ / ((4π)³ · P_min))^¼, computed in the SEM
  per beacon from editable emitter parameters (defaults 100 kW, 35 dBi, 10 GHz,
  −90 dBm) and an ownship RCS scenario control (default 5 m²). R_max is published in
  the beacon's maximum-range field. Detection = slant range ≤ R_max. An RCS change
  recomputes and retransmits every beacon. A beacon without emitter parameters keeps
  its explicit range.
- **B11 Airfields** (mission control; SSF airport selection). Seed table of airfields
  with ICAO code, name, latitude, longitude, runway heading and elevation:
  ETSI Manching, EDDM Munich Airport, EDDN Nürnberg. An optional local JSON file
  supplies per-ICAO partial overrides. A corrupt override file is reported and
  ignored.
- **B12 Simulation-support functions** (SSF actions; 01.10b, 01.04b). Run, pause,
  reset. Set position (latitude, longitude, altitude, speed, heading; ground or
  airborne). Snapshot save and load without aliasing between saved and live state.
  Fuel freeze. Telemetry capture to CSV with the field list of the model's
  `ssfLogTelemetry` documentation as the header, verbatim. Airport changes are
  accepted only while the simulation is frozen. Menu entries and the on-screen button
  grid trigger the same actions through one gate.
- **B13 Visual-system feed** (PCS to visual system). The flight state is packed into
  the visual system's native FDM structure (version, size and byte order per the
  model; otherwise measured against the tool's header) and sent decimated from the
  physics rate to the model's feed rate. The structure layout is established by
  measurement in the sandbox before use and re-checked by the suite.
- **B14 Tactical map** (ThreadMapWindow; 02.01, 02.03b). Slippy-map tiles from the
  model's tile server, fetched by a background thread into a disk cache. Ownship
  symbol at the centre. Each beacon drawn as a ring of radius R_max, colour-coded by
  classification, with the symbology shown in the screenshot.
- **B15 Ground-proximity attention getter** (PCS attention-getter strip; 02.04,
  01.08c). Evaluated every physics frame from height above ground and vertical speed.
  The warning is raised while the ownship is descending and the predicted time to
  ground impact (height above ground ÷ descent rate) is below a threshold (10 s if
  the model is silent), or while height above ground is below a floor and the
  ownship is descending. The warning clears when neither condition holds. While
  raised, the strip shows the warning text ('PULL UP') and flashes at about 2 Hz, in
  the colour shown in the screenshot. When not raised, the strip is present and
  blank. The warning is inhibited on the ground with the parking brake set.

**Conventions** (non-functional; cite as `[a: Conv]`).
pytest with offscreen Qt (`QT_QPA_PLATFORM=offscreen`). Tests bind the real ports
the model assigns; the "close running instances" caveat is documented. Long
end-to-end and autopilot tests are marked `slow`. Every spawned thread is stoppable
through a stop event and joined in teardown. Reference values are re-derived in the
test, not read back from the implementation. One virtual environment per interpreter
named in the model's runtime attributes, created by a setup script. Every script
invokes its interpreter by explicit path; no global activate. No version number of
any external tool appears in code or tests. Every sign, unit and property of an
external library (flight model, game-controller library, visual-system structure)
is measured in the sandbox before use and re-measured by the suite.

---

## 3. Prompt sequence

Issue in order. Each prompt assumes the previous deliverables are in the sandbox.
*Italic lines* are acceptance conditions, checked by the reviewer before proceeding.

**R-0 — Role and rules**
> You are the software developer of this project. Objective: develop a software
> application that complies with the attached design specification (SysML v2 model
> and requirements workbook). Rules: (1) The model and the workbook are the authority
> for structure, interfaces and values. The Behaviour Annex is the authority for
> behaviour and sequences where the model is silent. The screenshots are the
> authority for the appearance of the UI. Where these disagree, the model governs and
> you report the disagreement. (2) All generated code is traceable. Annotate with
> [a] = backed by the model, the workbook or the Annex (cite the ID or element);
> [b] = derived (state from what); [c] = own choice (flag inline, state what would
> settle it). (3) Assess each prompt for token feasibility and propose a step
> breakdown if needed. (4) Verify your outputs by executing them in the sandbox
> before delivery. Render visual outputs and inspect them. (5) Deliver files, not
> fragments. Target platform: Windows laptop; interpreters per the model's runtime
> attributes; physical HOTAS and visual system optional; display worst case 1280×720
> logical at 150 % scaling. Acknowledge and summarize the specification inventory:
> every model file, the workbook with its V-Method counts, both screenshots, the
> legacy artifacts.
*Acceptance: 18 model files (plus Functions/Addendum if supplied) listed with a
one-line summary each; workbook counts match Section 1, item 2; unreadable files
reported.*

**R-1 — Architecture**
> Derive the process architecture from the model: OS processes, threads per the TA
> thread decomposition, parts per thread, every inter-process link with port number,
> protocol and rate, each cited to the TA element stating it. Cross-check every port
> across the TA files: each `fsim_TA_Orch.sysml` node port must pair with the
> boundary port of the connected subsystem's TA file and with the interface
> requirements. Report inconsistencies; do not resolve them. Derive the code
> decomposition from the TA parts: one package for the application, one module set
> per TA subsystem, `tests/` beside it. The number and names of modules are derived
> from the model. List every TA part that has a user interface and the window or
> panel that presents it. Deliver an architecture memo with the link table and the
> planned file tree, every cell cited.
*Acceptance: every port cited to a model element; processes and threads match the
TA; every UI-bearing TA part named with its window; inconsistencies reported.*

**R-2 — Verification plan**
> From the workbook, extract every L2 requirement and its V-Method. Report the
> counts. For each requirement marked `T`, derive a test specification from the
> requirement statement and the model: stimulus, observation point, pass criterion,
> and reference values computed independently of the future implementation (a
> formula is verified against a hand computation; a rate is verified by measurement).
> Add conformance rows for the model's interface definitions and for B1–B3
> (round-trips, malformed rejection, tombstone, staleness, port constants against the
> model). Add one integration row per TA subsystem: start the application, assert the
> part exists and is wired to its declared peers. Add one fit row per window: the
> window fits and remains operable at the R-0 display worst case. For rows marked
> `I` or `D`, produce a one-line manual instruction; these are outside the automated
> suite. Deliver the plan as a table: requirement ID, V-Method, test name(s), pass
> criterion. The plan is frozen before implementation. A row changed later is
> amended explicitly with a reason.
*Acceptance: every `T` row has at least one test specification; no specification
references implementation internals the requirements do not name; integration and
fit rows present.*

**R-3 — Environments and common layer**
> Deliver the environment setup script and pinned manifests per the Conventions, one
> venv per interpreter named in the model's runtime attributes. Implement the common
> layer required by the TA's shared elements: port constants, each cited to the TA
> element stating it; message types per the model's interface definitions with JSON
> encode/decode and B1 validation; B2 protocol constants; the visual-system location
> policy of B4. Create `tests/` with a conftest (offscreen Qt, sys.path, a
> UDP-receive fixture, stop-event teardown) and a first module asserting: every port
> constant against its model element; message round-trips; malformed rejection;
> tombstone semantics; staleness at two refresh periods.
*Acceptance: suite green; a port test fails if any constant changes.*

**R-4 — Peripheral input transmitter (legacy code attached)**
> Implement the HOTAS transmitter against the HotasControl actions in
> `fsim_TA_Peripherals.sysml` and its allocated requirement 01.01b. Use the legacy
> file as evidence for axis mapping and debug printout style, and B5 for behaviour.
> Include a hardware-free demo source, debug and list-devices switches, the model's
> transmit rate with an optional stop event, and B5 hot-plug tolerance. Measure the
> game-controller library's axis signs and ranges in the sandbox before mapping them.
> Implement the plan rows allocated to this module.
*Acceptance: applicable plan rows green; no traffic on the peripheral port after
teardown.*

**R-5 — Physics thread**
> Implement ThreadPhysics per `fsim_TA_PCS_ThreadPhysics.sysml` (init phase and
> cyclic loop at the model's rate) and the airfield data of B11. Realize the
> flight-model identity named by the model; B6 ground and engine sequence; B5
> rudder and steering routing; B7 terrain setter; B13 visual-system feed; the SSF
> backend of B12. Measure every flight-model property you use (sign, unit,
> existence; e.g. the engine cutoff property name, which fails silently if wrong)
> before wiring it, and record the procedure. Cite 01.02, 01.02b, 01.04, 01.06b–e,
> 01.07 at the implementing lines. Implement the plan rows allocated here. A
> module-scoped ground-start fixture is recommended (flight-model init is slow).
*Acceptance: applicable plan rows green on the reviewer's machine.*

**R-6 — Bus orchestrator**
> Implement the BusOrchestrator per `fsim_TA_Orch.sysml` with B3 behaviour: the TA's
> nodes and port bindings, selectors loop, opaque forwarding, stop() safe on Windows.
> Implement the plan rows for 04.01: each relay path, tombstone transit, non-blocking
> behaviour under a datagram burst.
*Acceptance: applicable plan rows green; no unhandled-thread-exception warnings.*

**R-7 — Synthetic environment and tactical map**
> Implement the SyntheticEnvironmentMgr per `fsim_TA_SEM.sysml`: connect status;
> beacon inject, edit and remove GUI with B10 emitter parameters and live computed
> R_max; ownship RCS control; slant-range calculation; B2 event-driven transmission;
> `calcRadarEquation` citing 03.05. Implement ThreadMapWindow per its TA file with
> B14 behaviour. Implement the plan rows allocated here (02.01, 02.03b, 03.01–03.05,
> B2 and B10 conformance). For 03.05, reference values are hand-computed in the test
> from the requirement's formula, not copied from the implementation.
*Acceptance: applicable plan rows green.*

**R-8 — Pilot Control Station**
> Implement ThreadFlightSim per `fsim_TA_PCS_ThreadFlightSim*.sysml`. UIM parts,
> reproducing the reference screenshot as an image: the gauges and displays named in
> 01.08c (Airspeed, Attitude, Fuel, Heading, RPM, HUD view), drawn as the instruments
> shown in the image, not as text labels; attention-getter strip with B15; ownship
> readouts; virtual HOTAS; system, navigation, mission and sim control panels;
> message window. BkgIOM bridging UDP and the physics queues with B8 precedence and
> virtual fallback. B9 autopilot. B12 SSF menu and button grid. B4 visual-system
> launch. B7 terrain wiring. Construct the tactical map window here and make it
> visible: every UI-bearing part of the R-1 list is instantiated by the application,
> not only by a test. Cite 01.04b, 01.06, 01.06b–e, 01.08, 01.08b, 01.08c, 01.10,
> 01.10b, 01.11, 02.03, 02.04 at the implementing panels. Implement the plan rows
> allocated here, including the `T` part of 01.08c (each named gauge exists, is
> obtained from the application, and changes when the state it displays changes).
> Where a requirement names a UI element, the test drives the real widget offscreen,
> obtained from the application, not an internal API. Clamp every window to the R-0
> display worst case.
*Acceptance: applicable plan rows green, including the integration and fit rows.*

**R-9 — Integration deliverables and observation**
> Deliver the launcher (per-window venv activation; visual-system block per B4), the
> test runner (venv python by path, runs from root or `tests\`, pauses on exit,
> forwards pytest arguments), a README quick-start, and an end-to-end test: every TA
> process in one process space over the real ports. Assert: ownship reaches the SEM;
> demo controls reach the PCS; an injected beacon reaches PCS and map with correct
> detection; a tombstone clears both. Mark it `slow`. Then launch the assembled
> application headless as the launcher launches it. Enumerate its top-level windows
> against the R-1 UI list. Render each window at the display worst case, read the
> images back, compare them with the screenshots element by element, and list every
> discrepancy in an observation note delivered with the renders. Name the three most
> likely defects a five-minute run by the reviewer would expose, and check each.
*Acceptance: full suite green headless, twice in succession; every UI-bearing TA
part has a rendered window; discrepancies listed.*

**R-10 — Closure audit**
> Audit mechanically: (1) every `L2SYS-REQ-*` ID cited in source or tests exists in
> the workbook; (2) every cited model element exists in the model; (3) every workbook
> `T` row is cited by at least one test docstring; (4) every implemented test maps to
> a plan row or an explicit amendment; (5) every port constant matches the TA,
> re-derived from the model files; (6) every window of the R-1 UI list has a render.
> Output the six lists (all empty or complete) and the final test count, reported as
> an outcome. Open the summary with the R-9 observations, then the counts.
*Acceptance: zero dangling citations; full `T` coverage; plan and suite consistent;
every UI part rendered; suite green.*

**R-11 — Hardware verification (optional; requires target hardware)**
Checklist, not prompts. Physical HOTAS taxi (nose-wheel steering; gear, air-brake
and wheel-brake buttons). Three hot-plug sequences: start unplugged; unplug in
flight, virtual takeover within the precedence window; replug, physical reclaims.
Visual-system launch from the SSF button and terrain agreement while taxiing. Live
map tiles. Altitude-hold engage damping. Five minutes of free use on the target
display. These items correspond to workbook rows marked `I` or `D` and are outside
the automated acceptance.

---

## 4. Relation to earlier versions

Version 1 supplied the findings of the original interactive development as a
Decision Annex and produced working code. Version 3 removed all functional decisions
from the contract, replaced them with a gap-report process, and added 14
non-functional constraints, 10 implementation rules, 15 gates and an 11-check audit;
the code generation failed. Version 4 keeps the structure and length of Version 1,
adopts the specification baseline of Version 3 (18-file model, workbook v5), and
retains three rules from Version 3: the model owns structure and values; the
screenshots are authoritative as images; the application is launched and inspected
before it is reported complete. Behaviours the model cannot yet express are carried
in the Behaviour Annex. When the model gains activity and sequence diagrams, the
corresponding annex entries are retired. The prompt sequence does not change.
