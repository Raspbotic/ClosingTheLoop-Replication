# Flight Simulator Replication Contract — Version 2

Protocol for regenerating the flight-simulator application and its regression suite from the SysML v2 model, the requirements workbook and the Decision Annex, executed by an LLM agent with a code-execution sandbox.

---

## 1. Scope

This document defines the acceptance criteria (§3), the inputs (§4), the prerequisites (§5), the decisions the model does not carry (§6), the prompt sequence (§7), the implementation rules (§8), the gates (§9) and the outputs (§10).

A run is accepted when gates G1–G9 pass. Acceptance is defined on the criteria of §3, not on the text of the generated code.

The whole document is attached to the agent.

---

## 2. Definitions and conventions

| Term | Meaning |
|---|---|
| Agent | The LLM with a code-execution sandbox that executes the prompt sequence. |
| Practitioner | The person who assembles the package, issues the prompts and checks the gates. |
| Sandbox | The agent's execution environment. Never evidence about the target. |
| Target | The Windows host of §5.1 with its two interpreters. |
| Model | The SysML v2 files of §4.1. LA: logical architecture. TA: technical architecture. |
| Workbook | The requirements workbook of §4.2. |
| Requirement id | Workbook form `L2SYS-REQ-nn.nn`, suffix `b` where the workbook uses it. `nn.nn` here means `L2SYS-REQ-nn.nn`. |
| Decision id | `Dn`, §6. A Decision marked `[provisional]` is executable as written and awaits customer confirmation. |
| Plan | The verification plan delivered at R-2: frozen rows, numbered amendments `A-n`, and an Additions register (Appendix A). |

Notation. Text in `> block quotes` is a prompt, issued verbatim. Text in *italics* after a prompt is that step's acceptance line.

Provenance tags. Every statement about the specification, a tool or a language carries one tag, in code and in prose: **[a]** backed by a supplied artifact, cited (`[a: L2SYS-REQ-nn.nn]`, `[a: Dn]`); **[b]** derived from [a]; **[c]** the agent's own choice, flagged inline with what would settle it.

Execution modes. Either the practitioner issues prompts one at a time and checks each acceptance line, or attaches this document to one initiating prompt and the agent executes in order, reporting each acceptance line. The mode, the agent's model identifier and the elapsed time are recorded in `docs/RECREATION_LOG.md`.

---

## 3. Acceptance criteria

1. **Architecture.** The process and port architecture is identical to the TA and to D4.
2. **Traceability.** Every module under `flightsim/` carries [a]/[b]/[c] annotations; every cited requirement id exists in the workbook.
3. **Verification.** A pytest suite derived from the requirements before implementation passes headless and cites requirement ids in test docstrings; every workbook `T` row is covered by at least one test that verifies the requirement statement. The number of tests is an outcome of the plan, not a target.
4. **Interoperability.** The wire protocol is exactly that of D1–D4.
5. **Model integrity.** The model loads with zero errors in the SysML v2 Pilot Implementation before R-1.
6. **Environment independence.** The application and the complete suite run to completion with no FlightGear installed and no HOTAS attached. Only *LAUNCH 3D OTW* and the manual `I`/`D` rows may depend on either.
7. **Output hygiene.** A full suite run creates, modifies and deletes nothing outside `tmp_path`.
8. **Deployability.** Every pin is installable on the target interpreter and platform, established from package metadata for that target.

---

## 4. Package: inputs attached before R-0

### 4.1 SysML v2 model — 18 required, 1 optional

| Layer | n | Files |
|---|---|---|
| Logical architecture, use cases | 2 | `fsim_LA.sysml`, `fsim_UCvsLA.sysml` |
| Technical architecture | 12 | `fsim_TA_Attributes.sysml`, `fsim_TA_Ports.sysml`, `fsim_TA_System.sysml`, `fsim_TA_PCS.sysml`, `fsim_TA_PCS_ThreadFlightSim.sysml`, `fsim_TA_PCS_ThreadFlightSim_UIM.sysml`, `fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml`, `fsim_TA_PCS_ThreadPhysics.sysml`, `fsim_TA_PCS_ThreadMapWindow.sysml`, `fsim_TA_Orch.sysml`, `fsim_TA_SEM.sysml`, `fsim_TA_Peripherals.sysml` |
| Requirements | 4 | `fsim_StakeholderRequirements.sysml`, `fsim_SystemRequirements.sysml`, `fsim_TA_IF_Requirements.sysml`, `fsim_R2F.sysml` |
| Optional | 1 | `fsim_Functions.sysml` |

`fsim_RVM.sysml` is attached only as an evidence-free build, or not at all. A `.txt` suffix appended for transport is transparent; the agent restores the `.sysml` name on disk.

### 4.2 Requirements workbook

`fsim_Design_v5.xlsx`. Authoritative for the requirements baseline. 34 L2 rows: 25 `T`, 2 `T, I`, 5 `I`, 2 `D` — 27 carry `T`, 9 carry `I` or `D`. Zero-padded ids, base/`b` convention, both R2F matrices.

### 4.3 Reference GUI screenshots

`Desired_UIUX_capture.JPG`, `Desired_FGear_capture.JPG`. Authoritative for **layout** of the panel requirements (01.06, 01.08x, 01.10x, 01.11) and D14. Not an architecture source, not authoritative for data.

### 4.4 Legacy HOTAS transmitter

`hotas_standalone.py`. Authoritative for the axis indices (roll 0, pitch 1, throttle 2, yaw candidates 3 and 4), the throttle normalization, and the debug-printout format including its on-change suppression. Not authoritative for signs, for the wire format (D1) or for the button map (D7).

### 4.5 Reference environment manifests — inputs, not deliverables

Attached before R-0. **Not** what R-3 produces; R-3 produces `requirements_main.txt` and `requirements_hotas.txt`, which appear in §10. Nothing in this section appears in §10.

| File | Contents |
|---|---|
| `reference_venv_fsim_freeze.txt` | `pip freeze` of a working `.venv_fsim` |
| `reference_venv_hotas_freeze.txt` | `pip freeze` of a working `.venv_hotas` |
| `reference_venv_fsim_wheels.txt` | interpreter version, platform, installed wheel tag per package |
| `reference_venv_hotas_wheels.txt` | the same for `.venv_hotas` |

Captured on the target, each environment's own interpreter by explicit path:

```bat
.venv_fsim\Scripts\python.exe -m pip freeze > reference_venv_fsim_freeze.txt
.venv_fsim\Scripts\python.exe -c "import importlib.metadata as m,sys,platform;print(sys.version);print(platform.platform());[print(d.metadata['Name'],d.version,[l.split(':',1)[1].strip() for l in (d.read_text('WHEEL') or '').splitlines() if l.startswith('Tag:')]) for d in m.distributions()]" > reference_venv_fsim_wheels.txt
```

When no reference environment exists, the practitioner states so in writing in the initiating prompt and D22 applies.

### 4.6 This document

Attached in full.

### 4.7 Package check

```bash
grep -rl "test_" <package>/model/                            # expect: no matches
ls <package> | grep -E '^requirements_(main|hotas)\.txt$'    # expect: no matches
comm -12 <(ls <package>) <(§10 file list)                    # expect: empty
```

---

## 5. Practitioner prerequisites

### 5.1 Required

| Item | Requirement |
|---|---|
| Agent | LLM with a code-execution sandbox able to run Python, install packages, execute a test suite. |
| Target host | Windows. |
| Python 3.14 | Resolved by `py -3.14`. |
| Python 3.11 | Resolved by `py -3.11`. |
| SysML v2 tooling | Pilot Implementation (Eclipse plug-in, JDK 21+), on the practitioner's machine, for R-0b and G1. Not expected of the agent. |
| Network access | Once, to install the Python dependencies. |

### 5.2 Virtual environments

Neither exists on a fresh machine; both are created by `setup_venvs.bat` (R-3).

| | `.venv_fsim` | `.venv_hotas` |
|---|---|---|
| Interpreter | Python 3.14 | Python 3.11 |
| Runs | PCS, orchestrator, SEM, the whole pytest suite | `flightsim.hotas_standalone` only |
| Manifest | `requirements_main.txt` | `requirements_hotas.txt` |
| Contents | `jsbsim`, `numpy`, `PyQt5` (+ `PyQt5-Qt5`, `PyQt5_sip`), `requests` (+ `certifi`, `charset-normalizer`, `idna`, `urllib3`), `pytest` (+ `iniconfig`, `pluggy`, `packaging`, `Pygments`), `colorama` — 16, no pygame | `pygame` — 1 |

Both manifests are pinned (`==`) and validated per IR-6. `requirements_main.txt` includes the test dependencies. No script performs a global `activate`; every script and tool invokes its interpreter by explicit path.

### 5.3 Optional

| Item | Behaviour when absent |
|---|---|
| FlightGear (any version) | *LAUNCH 3D OTW* reports that FlightGear is not configured and does nothing. Everything else runs normally. |
| YF-16 aircraft package | FlightGear starts with a default aircraft; a warning is issued. |
| Physical HOTAS | The Virtual HOTAS (D10) drives the aircraft. |

No FlightGear version number appears in the code or the tests.

---

## 6. Decision Annex

Customer decisions not derivable from the model. Cited as `[a: Dn]`.

Every Decision below is answerable by the customer or the architect before any implementation exists. No Decision records the outcome of a previous run.

**D1 Wire format.** JSON-over-UDP dataclasses: `HotasControls` (seq; roll, pitch, yaw ∈ [−1, 1]; throttle ∈ [0, 1]; switches dict), `OwnshipState` (fields per D19), `RadarBeacon` (beacon_id, classification FRIEND/FOE/UNKNOWN, lat, lon, alt, max_range_m, detected, slant_range_m, active). A malformed datagram is rejected with a dedicated error type of the application's own. Ranges are enforced on decode as well as on construction.

**D2 Beacon protocol.** Event-driven: only the changed beacon is transmitted. Deletion is a tombstone (`active=False`). Background refresh every 2.0 s. The receiver discards beacons stale for more than 5.0 s (stale ≥ 2 × refresh). Every display of the beacon roster reflects both its insertions and its removals.

**D3 Orchestrator.** Self-contained Python `selectors` UDP relay, no external middleware; two internal nodes per the TA (seNode, pcsNode); payload-opaque; non-blocking; stoppable on Windows. The relay is an intermediate process: SEM and PCS never communicate directly.

**D4 Port map.** HOTAS→PCS 5005; PCS→orchestrator 5006; orchestrator→SEM 5007; SEM→orchestrator 6007; orchestrator→PCS 6006; PCS→FlightGear native-fdm 5550 (FGNetFDM version 24, 408 bytes, big-endian, decimated to about 60 Hz); FlightGear httpd 8080, ground elevation at `/json/position/ground-elev-m`, the value arriving as a number, a string, or wrapped in an object under `value`; OSM tiles at `https://tile.openstreetmap.org`.

**D5 Runtimes.** Two virtual environments per the TA `PythonRuntime` attributes: `.venv_fsim` (3.14: PCS, orchestrator, SEM, pytest suite) and `.venv_hotas` (3.11: HOTAS transmitter). `setup_venvs.bat` creates both and installs both pinned sets. The launch script activates one environment per window; no global activate; interpreters by explicit path.

**D6 Aircraft and OTW view.** JSBSim model `f16`. FlightGear visual model `YF-16` from a user aircraft directory. Launch command:

```
<resolved fgfs> --fdm=null --native-fdm=socket,in,60,,5550,udp
                [--fg-aircraft=<dir>] --aircraft=YF-16 --airport=<ICAO>
                --timeofday=noon --httpd=8080
                --prop:/input/joysticks/js[0]=0 --prop:/input/joysticks/js[1]=0
                [--fg-root=<dir>]
```

`argv[0]` is the executable resolved per D16. A bracketed argument appears only when its variable is set. `--fg-aircraft` takes the parent directory; FlightGear scans one level below it for `YF-16/YF-16-set.xml`. No FlightGear version is pinned.

**D7 Control mapping.** btn2 = landing-gear toggle; btn4 = air-brake toggle; btn6 = momentary wheel brake. Yaw drives both `fcs/rudder-cmd-norm` and `fcs/steer-cmd-norm`. HOTAS yaw axis: the larger deflection of candidate axes 3 and 4. The transmitter puts raw pressed states on the wire; toggle edges for btn2 and btn4 are detected by the receiver; a held button does not re-toggle.

**D8 Ground and engine semantics** (01.04, 01.06b, 01.07). Spawn on the runway with the engine off, parking brake on, gear down. At `t0` weight is on the wheels and ground speed is zero. Engine start is refused above 10 % throttle; exactly 10 % starts. SSF set-position airborne implies engine on and parking brake released. Reset returns the clean state — autopilot and fuel freeze disengaged — and publishes one telemetry frame and one FlightGear frame even while flight-frozen.

**D9 Terrain loop.** Poll FlightGear ground elevation at 2 Hz; feed it into the FDM terrain elevation, slewed at most 1 ft per 120 Hz frame. With no FlightGear the poll is inactive and the physics runs unaffected. A failed poll is tolerated.

**D10 Virtual HOTAS** (01.08b). Physical-precedence window 1.0 s: a fresh datagram on 5005 takes precedence; the widget greys out and its title changes. Stick and pedals return to neutral on any mouse release. On loss of the physical device, stick and pedals centre and the throttle retains its last position. The widget mirrors live physical commands at 10 Hz while locked; mirroring is display-only. Throttle scale IDLE 0 / 25 / 50 / 75 / MIL 100 with an afterburner marker at the top of travel. Drag-down = stick back = pitch +1.

**D11 Autopilot** (01.11). Engaging AP, AH or AT captures the current value as target. A mode that is not engaged writes nothing.

```
AH:  vs_cmd    = clip(0.15·alt_err, ±5 m/s)
     theta_cmd = clip(1.5·(vs_cmd − vs), −8°, +12°)
     pitch     = clip(0.06·(theta_cmd − pitch_deg), ±1)
AP:  bank_cmd  = clip(0.8·hdg_err, ±25°)
     roll      = clip(0.03·(bank_cmd − roll_deg), ±1)
AT:  throttle  = clip(0.5 + 0.04·spd_err, 0, 1)
```

`hdg_err` is the shorter arc, wrapped into [−180°, +180°).

**D12 Radar equation** (02.02 → 03.05, computed in the SEM).

```
R_max = (Pt·G²·λ²·σ / ((4π)³·P_min·L))^¼        λ = c / f
```

published in `max_range_m`. Detection = slant range ≤ R_max, inclusive. Per-beacon editable Pt, G, f, P_min (defaults 100 kW, 35 dBi, 10 GHz, −90 dBm) and L (default 1.0). Frequency is the editable parameter; wavelength is displayed derived. Ownship RCS is a scenario spinbox, default 5 m², giving about 39 km at the defaults; a change of RCS recomputes and retransmits every beacon. Beacons without emitter parameters keep their explicit range and are not recomputed on an RCS change.

**D13 Airports.** `[provisional]` Seed table:

| ICAO | Name | Lat (°) | Lon (°) | Runway hdg (°) | Elevation (m) |
|---|---|---|---|---|---|
| LIML | Milano Linate | 45.4451 | 9.2767 | 176.0 | 107.6 |
| LIMC | Milano Malpensa | 45.6306 | 8.7281 | 350.0 | 234.1 |
| KSFO | San Francisco Intl | 37.6188 | −122.3750 | 284.0 | 4.0 |
| EDDF | Frankfurt am Main | 50.0333 | 8.5706 | 250.0 | 111.0 |

Optional `airports_local.json` with per-ICAO partial overrides: an unmentioned key keeps its seed value. Tolerated without failing the load: a corrupt file, a corrupt value inside a valid file, and a partial row for an unknown ICAO (ignored). A complete row for an unknown ICAO is added.

**D14 PCS layout** (per the reference screenshot). Attention-getter strip; tabulated Ownship State readouts (fixed-width, right-aligned); Virtual HOTAS on the left; System Controls (state-coloured PRK BRK / ENGINE / AIR BRAKE / GEAR); Navigation Controls (Alt/Spd/Hdg with AH/AT/AP); Mission Control (airport combo box, freeze-gated); Sim Control SSF button grid (RUN / PAUSE / RESET / LAUNCH 3D OTW / SET POSITION / Save Snapshot / Load Snapshot / FUEL FREEZE / Telemetry Capture); gauge column (Airspeed, Attitude, Altitude, Fuel, Heading, RPM, HUD line) per 01.08c; message log box; dark QSS. The SSF grid and the SSF menu command identical behaviour. The attention-getter strip is empty in the nominal case.

Telemetry CSV header, 28 fields in order:

```
wall_time_iso, sim_time_s, model, lat_deg, lon_deg, alt_m, agl_m,
v_speed_mps, speed_mps, heading_deg, target_heading_deg, pitch_deg,
pitch_rate_dps, roll_deg, target_alt_m, target_speed_mps,
aileron_cmd, elevator_cmd, rudder_cmd, throttle_cmd, gear_cmd,
trim_pitch, trim_roll, trim_yaw,
ap_engaged, ah_engaged, at_engaged, warning_state
```

**D15 Test conventions.** pytest. Offscreen Qt (`QT_QPA_PLATFORM=offscreen`). Tests bind the real UDP ports; the "close running instances" caveat is documented. Long end-to-end and convergence tests are marked `slow`. Every spawned thread is stoppable (`stop_event`). Requirement ids are cited in test docstrings, or in the module docstring where a module verifies one requirement throughout. The runner invokes the environment's Python by path and pauses on exit. The suite runs in `.venv_fsim`.

**D16 FlightGear location and optionality.** No installation and no version is assumed. Location from the environment: `FSIM_FG_EXECUTABLE` (full path, used verbatim, highest precedence), `FSIM_FG_HOME` (install root, `<root>\bin\fgfs.exe` derived), `FSIM_FG_AIRCRAFT_DIR` (the `--fg-aircraft` parent), `FSIM_FG_ROOT` (optional data directory). Resolution order: explicit executable → install root → `PATH` → scan of `C:\Program Files\FlightGear*\bin\fgfs.exe` newest first, ordered on the version in the directory name → the bare program name of D6, reported as unresolved. Values are seeded in `fsim_env.bat`, which the launchers `call` and the code parses directly; an exported variable beats the file. A missing executable blocks only *LAUNCH 3D OTW* and reports the variable to set; a missing aircraft directory warns.

**D17 FDM property conventions.** No sign, unit or existence of an FDM property is assumed from its name or from any other document. Before a property is wired, its sign and units are measured by running the FDM in the sandbox, and the measurement is recorded in `docs/FDM_MEASUREMENTS.md` and at the implementing line. Where a measured convention contradicts a pilot-side convention fixed by a Decision, the Decision governs the pilot side and the implementation reconciles the two at the boundary.

**D18 Test hermeticity.** No test depends on the machine it runs on. Tests do not read the real `PATH`, do not touch the filesystem outside `tmp_path`, and do not spawn a real external process. Every path the application writes to by default is a parameter the suite can redirect. Deployment rows pass identically with and without FlightGear installed.

**D19 OwnshipState fields.** `seq`, `sim_time_s`, `lat_deg`, `lon_deg`, `alt_m`, `agl_m`, `heading_deg`, `pitch_deg`, `roll_deg`, `pitch_rate_dps`, `speed_mps`, `vspeed_mps`, `rpm_pct`, `fuel_lbs`, `engine_running`, `parking_brake`, `air_brake`, `gear_down`, `on_ground`, `aileron_cmd`, `elevator_cmd`, `rudder_cmd`, `throttle_cmd`, `trim_pitch`, `trim_roll`, `trim_yaw`, `model`. Angles in degrees, distances in metres, speeds in m/s, fuel in pounds, `rpm_pct` a percentage.

**D20 Airspeed convention.** `[provisional]` `speed_mps` in `OwnshipState`, the *Spd* autopilot target, and the *Speed* field of SSF *SET POSITION* are all true airspeed. The Airspeed gauge may additionally display knots.

**D21 Engine state.** `engine_running` is the state commanded by the ENGINE toggle. Measured engine speed drives the RPM gauge only.

**D22 No reference environment.** When §4.5 supplies no manifests and the practitioner has stated so in writing, the agent pins the newest release of each package satisfying IR-6, records every pin as `[c]`, and raises an open item naming `pip freeze` on the target as what would settle it. It does not pin from what its sandbox has installed.

**D23 Ground-proximity condition.** `[provisional]` The 02.04 attention getter asserts when the aircraft is airborne, descending faster than 2 m/s, and height above ground divided by rate of descent is under 10 s; never above 900 m AGL; never on the ground. It flashes with a period of about 0.6 s while the condition persists and blanks otherwise.

**D24 Tactical map.** `[provisional]` 256-pixel Web-Mercator tiles; default zoom 12; zoom clamped 2–18. Threat rings: FRIEND green, FOE red, UNKNOWN amber; an undetected contact is drawn dashed in the same colour. Tiles are cached to disk and a cached tile is never re-fetched. With no network the map draws placeholders and raises nothing.

---

## 7. Prompt sequence

### R-0 — Role and rules

> Role: software developer of this project. Objective: develop a software application that complies with the attached design specification (SysML v2 model and requirements workbook).
>
> Rules:
> 1. All generated code is traceable to specification items. Annotate [a] = backed by the specification or the Decision Annex (cite the id), [b] = derived, [c] = own choice (flag inline, say what would settle it).
> 2. The same tagging applies to prose.
> 3. The rules of §8 hold throughout the run.
> 4. Assess each prompt for feasibility within your context and output limits; propose a step breakdown if needed.
> 5. Verify your outputs by executing them in your sandbox before delivering them.
> 6. Deliver files, not fragments; name the files a fix touches; never paste diff notation into a source file.
>
> Target platform: a Windows laptop with Python 3.14 and 3.11 installed; no FlightGear, no HOTAS, no virtual environments. The application and its whole test suite work in that state. Your sandbox is not the target and is never evidence about it.
>
> List every attached file and state what each contributes. State your model identifier.

*Acceptance: the inventory names the 18 required model files, the workbook, both screenshots, the legacy HOTAS file, and either the four §4.5 manifests or a statement that they were not supplied. Any unreadable file is reported here.*

### R-0b — Model intake

> Read every attached `.sysml` file end to end. Deliver `docs/MODEL_INTAKE.md`: a one-line summary of each file, and a table of each file, the package it declares, and any package declared by more than one file.
>
> State whether you have a SysML v2 parser, having checked rather than assumed; report what you ran. If you do not, every claim about SysML v2 syntax is [c] unless it quotes a supplied file or a supplied tool output, and the intake report states which classes of error your reading cannot detect.

The practitioner loads the model into the Pilot Implementation and, if there are errors, supplies the exported problem list (Description / Resource / Path / Location / Type) as data. With no parser and no problem list the agent proceeds from R-1, tags syntax claims [c], and raises an open item that G1 is unrun.

*Acceptance: every model file summarized; duplicate package declarations reported; parser availability stated with its evidence; the model loads with zero errors, or the open item exists.*

### R-1 — Architecture

> Derive the process architecture: operating-system processes, threads per the TA thread decomposition, and all inter-process links with port numbers. Cross-check every port across the TA files — each node port in `fsim_TA_Orch.sysml` against the boundary port of the connected subsystem, and against D4. Report any inconsistency; do not resolve it silently. Deliver `docs/ARCHITECTURE.md` with a link table (source, sink, port, protocol, rate) and the planned file tree (package `flightsim/`, subpackage `pcs/`, `tests/` beside it).

*Acceptance: the link table is identical to D4; six processes (HOTAS, PCS, orchestrator, SEM, FlightGear, OSM); PCS threads FlightSim, Physics and MapWindow per the TA; inconsistencies reported, not resolved.*

### R-2 — Verification plan

> From the workbook, extract every L2 requirement and its V-Method; report the counts. For each `T` row derive a test specification from the requirement statement alone: stimulus, observation point, pass criterion, and reference values computed independently of the future implementation — a formula verified against a hand computation of that formula written out in the test file, a rate verified by measuring that rate.
>
> Add conformance checks for D1–D4 cited `[a: Dn]`. Add hermeticity rows for D18, including at least one that fails if the suite depends on FlightGear being installed.
>
> For `I` and `D` rows produce a one-line manual instruction with a stable id `M-n`.
>
> Deliver `docs/VERIFICATION_PLAN.md`: requirement id, V-Method, test name(s), pass criterion. The plan is frozen before implementation. A frozen row may be weakened, changed or removed only by a numbered amendment `A-n`. Rows added during implementation go in the Additions register, grouped by subsystem, one reason per group, each group stating whether it was written before or after the code it covers.

*Acceptance: every `T` row has a test specification; no specification references implementation internals the requirements do not name; every `I`/`D` row has an `M-n`; the reported counts match §4.2.*

### R-3 — Runtime environments and common layer

> Deliver:
> * `requirements_main.txt` and `requirements_hotas.txt`, pinned per §5.2, derived from the §4.5 manifests where supplied, otherwise per D22.
> * `tools/check_wheels.py`: reads a requirements file and confirms from package metadata that every pin is installable on a stated target interpreter and platform tag. Report its output for both manifests against the target.
> * `setup_venvs.bat`: creates both environments with `py -3.14` and `py -3.11`, upgrades pip, installs both manifests, seeds `fsim_env.bat` from a template, reports what it found. Idempotent; fails with a message naming a missing interpreter rather than a traceback.
>
> Then implement `flightsim/common/ports.py` (D4 constants, each cited to its TA source), `flightsim/common/messages.py` (D1 and D19 dataclasses, JSON encode and decode, D2 constants) and `flightsim/common/config.py` (D16). Create `tests/` with a conftest satisfying D15 and D18, and a first module asserting: every port constant against D4; message round-trips; rejection of malformed datagrams; tombstone semantics; stale ≥ 2 × refresh; the D16 resolution rows.

*Acceptance: `check_wheels.py` reports every pin installable on the target; on a machine with only the two interpreters, `setup_venvs.bat` then the suite is green with no manual step between; the suite passes identically with and without FlightGear installed.*

### R-4 — HOTAS transmitter

> Implement `flightsim/hotas_standalone.py` against 01.01b and the `HotasControl` actions of `fsim_TA_Peripherals.sysml`, per §4.4 and D7. Import pygame lazily so the module stays importable from `.venv_fsim`; the suite asserts this. Include `--demo` (hardware-free synthetic source), `--debug`, `--debug-axes`, `--list`; a 60 Hz transmit loop with an optional `stop_event`; hot-plug tolerance — start without a device and wait, suspend on `JOYDEVICEREMOVED`, resume on reconnect with a 1 Hz rescan fallback. Implement the plan rows for 01.01b and the D7 yaw-selection check.

*Acceptance: the applicable plan rows are green with no joystick attached and pygame absent; no traffic on 5005 after teardown.*

### R-5 — Physics thread

> Implement `flightsim/pcs/physics.py` (`ThreadPhysics` per its TA file: initialization phase and 120 Hz cyclic loop) and `flightsim/pcs/airports.py` (D13). Realize D6, D8, D9, D20, D21, the D7 button map with the dual rudder/steer routing, FGNetFDM v24 packing decimated to 60 Hz on 5550, and the SSF backend methods. The FDM stream is written whether or not anything is listening; nothing here requires FlightGear.
>
> Apply D17 to every FDM property you wire. Cite 01.02b, 01.04, 01.06b–e and 01.07 at the implementing lines. Implement the plan rows allocated to this step.

*Acceptance: the applicable plan rows are green; `docs/FDM_MEASUREMENTS.md` records a measurement, with the procedure that produced it, for every FDM property the implementation writes or reads.*

### R-6 — Bus orchestrator

> Implement `flightsim/master_node.py` (`BusOrchestrator` per `fsim_TA_Orch.sysml`, D3): `seNode` and `pcsNode` with the TA port bindings, a `selectors` loop, opaque forwarding, a `stop()` safe on Windows. Implement the plan rows for 04.01.

*Acceptance: the applicable plan rows are green; no unhandled-thread-exception warnings; the module imports only the standard library.*

### R-7 — SEM and tactical map

> Implement `flightsim/run_sem.py` (`SyntheticEnvironmentMgr` per `fsim_TA_SEM.sysml`: connection status; beacon inject, edit and remove GUI with the D12 parameters and the live R_max; ownship RCS control; slant range; event-driven transmission per D2; `calcRadarEquation` citing 03.05) and `flightsim/pcs/map_window.py` (`ThreadMapWindow` per its TA file: `MapGuiWidget` and a `TileFetcher` QThread, slippy-map arithmetic per D24, disk cache, ownship symbol, threat rings citing 02.03b). Keep the model layer of both free of Qt.
>
> Implement the plan rows for 02.01, 02.03b, 03.01–03.05 and the D2/D12 conformance. For 03.05 the reference values are computed in the test from the formula in the requirement, and the scaling laws are checked as well as sampled points. Network fetches are verified against a loopback stub, never the live service.

*Acceptance: the applicable plan rows are green with no external network access.*

### R-8 — Pilot Control Station

> Implement `flightsim/pcs/main_window.py` and `flightsim/pcs/virtual_hotas.py`: the D14 layout; an IOManager bridging the UDP and physics queues with D10 precedence and virtual fallback; the D11 autopilot; the SSF menu and grid gated identically by freeze; snapshot save and load; the D14 telemetry CSV; FlightGear launch per D6 and D16; terrain wiring per D9; the D23 attention getter.
>
> *LAUNCH 3D OTW* is the only control that may depend on FlightGear. With none installed it reports which variable to set and leaves the simulation running; it never raises and never exits.
>
> Cite 01.06, 01.06b–e, 01.08, 01.08b, 01.10, 01.10b, 01.11, 02.03 and 02.04 at the implementing panels. Where a requirement names a UI element, the test drives the real widget offscreen, not an internal API.

*Acceptance: the applicable plan rows are green; the SSF buttons and menu items produce identical state changes; the PCS starts and flies with no FlightGear present; the run writes nothing outside `tmp_path`.*

### R-9 — Integration deliverables

> Deliver `launch_flightsim.bat` (per-window activation per D5), `run_tests.bat` (environment Python by path, pauses on exit, forwards pytest arguments), `fsim_env.bat.template` (D16), and an end-to-end test: orchestrator, PCS, SEM and demo HOTAS in one process space over the real ports, asserting that ownship state reaches the SEM, demo controls reach the PCS, an injected beacon reaches the PCS and the map with correct detection, and a tombstone clears both (marked `slow`). Deliver `README.md` with a quick-start that works with only the two interpreters.

*Acceptance: the full suite is green headless; a second run is green; green with and without FlightGear and with no joystick; the working tree is unchanged by the run.*

### R-10 — Closure audit

> Deliver `tools/closure_audit.py`, checking:
> 1. every `L2SYS-REQ-*` id cited in `flightsim/` or `tests/` exists in the workbook;
> 2. every workbook `T` row is cited by a test docstring, or by the docstring of a module verifying it throughout;
> 3. every implemented test maps to a frozen plan row, an amendment, or the Additions register, and no frozen row has been removed without an amendment;
> 4. every port constant matches the TA;
> 5. every workbook `I`/`D` row has a recorded, attributed result in `docs/MANUAL_RESULTS.md`;
> 6. every module under `flightsim/` carries [a]/[b]/[c] annotations; the census is written to `docs/PROVENANCE.md` with the count and share of each tag, the distinct citation targets, and the full inventory of every [c], stating that counts are tag occurrences and the classification is self-reported.
>
> The audit's evidence is the source files, the workbook and the plan's own rows. Demonstrate on a fixture that it neither credits nor charges an item that is only illustrative.
>
> Output the six lists and the test count (functions and pytest items, separately). Check 5 reports outstanding rows as OPEN by default; `--strict-manual` makes them fail. The workbook path defaults to the highest-numbered `spec/fsim_Design_v*.xlsx`.

*Acceptance: checks 1–4 and 6 empty or complete; the suite green; check 5 lists the `I`/`D` rows as OPEN; the fixture demonstration passes.*

### R-11 — Hardware verification

Executed by the practitioner on the target hardware; not a prompt. Items `HW-1` to `HW-8` in `docs/MANUAL_VERIFICATION.md`: physical HOTAS taxi (nose-wheel steering, btn2, btn4, btn6); three hot-plug sequences (start unplugged; unplug in flight, virtual takeover within about 1 s with the throttle retained; replug, physical reclaims control); FlightGear launch from the SSF button and terrain agreement while taxiing; OSM live tiles; AH engagement damping; ground-proximity attention getter.

### R-12 — Manual verification record

> Deliver `docs/MANUAL_VERIFICATION.md` (one `M-n` row per workbook `I`/`D` requirement, plus the `HW-n` checklist) and `docs/MANUAL_RESULTS.md` (Id / Req / Date / Tester / Build / Result / Evidence, one row per item per run). `Result` ∈ {PASS, FAIL, BLOCKED, NOT RUN, N/A}. A PASS without a date and a tester is invalid. A re-run adds a row; earlier rows are not edited.

*Acceptance: `closure_audit.py --strict-manual` passes only when every `I`/`D` row carries an attributed PASS.*

---

## 8. Implementation rules

| Id | Rule |
|---|---|
| IR-1 | Tag every statement about the specification, a tool or a language; an unsupported one is [c] with a note of what would settle it. |
| IR-2 | Establish every claim about the execution environment by running a command and recording its output; an unexecuted environment claim is a defect, not a [c]. |
| IR-3 | With no parser and no problem list, proceed under R-0b's fallback; never infer SysML v2 syntax rules. |
| IR-4 | Test any check whose behaviour differs between sandbox and target in both states, and negative-check it by reintroducing the defect. |
| IR-5 | Reference requirements by their stable alias, never by a name derived from the requirement text. |
| IR-6 | Validate every pin against the target interpreter and platform tag from package metadata, never from a successful install in the sandbox. Accept pure-python, version-agnostic-platform, stable-ABI-at-or-below-target and exact-tag wheels; reject a free-threaded-only tag. |

---

## 9. Acceptance gates

| Gate | Command | Pass condition | Criterion |
|---|---|---|---|
| G1 | Load the full §4.1 package in the SysML v2 Pilot Implementation | problem list empty | 5 |
| G2 | On a host with only Python 3.14 and 3.11: `setup_venvs.bat` then `run_tests.bat` | both succeed, no manual step between | 6, 8 |
| G3 | `run_tests.bat` twice, after the last edit of the run | green both times | 3 |
| G4 | `run_tests.bat` with no FlightGear and no joystick, then again with a FlightGear-shaped installation present | green, identical counts | 6 |
| G5 | `closure_audit.py` | checks 1–4 and 6 empty or complete; `docs/PROVENANCE.md` present | 1, 2 |
| G6 | `closure_audit.py` check 4, and read `ports.py` | every port matches D4 | 1, 4 |
| G7 | Read the session transcript against `docs/VERIFICATION_PLAN.md` | the plan predates R-3; no frozen row weakened or removed without an amendment; every added row in the Additions register | 3 |
| G8 | `closure_audit.py --strict-manual` | exit 0 | manual rows |
| G9 | `git status --porcelain` after a full suite run | empty | 7 |

A run passing G1–G7 and G9 is code-complete against criteria 1–8. G8 closes verification. G1 and G2 are the practitioner's; an agent that cannot run them records an open item and reports them neither as passed nor as blocking.

---

## 10. Outputs of a run

```
flightsim/                    application package, decomposed per R-1
tests/                        conftest and one module per subsystem
tools/closure_audit.py        R-10
tools/check_wheels.py         R-3
docs/ARCHITECTURE.md          R-1
docs/MODEL_INTAKE.md          R-0b
docs/VERIFICATION_PLAN.md     R-2, frozen, amendments + Additions register
docs/FDM_MEASUREMENTS.md      R-5
docs/MANUAL_VERIFICATION.md   R-12 procedure
docs/MANUAL_RESULTS.md        R-12 record
docs/CLOSURE_AUDIT.md         R-10 output
docs/PROVENANCE.md            R-10 check 6
docs/RECREATION_LOG.md        mode, agent model identifier, prompts issued
                              outside this document, elapsed time, defect
                              rounds, observations, open items
setup_venvs.bat               R-3
requirements_main.txt         R-3
requirements_hotas.txt        R-3
launch_flightsim.bat          R-9
run_tests.bat                 R-9
fsim_env.bat.template         R-9
README.md                     R-9
```

`docs/RECREATION_LOG.md` is mandatory. No file in this list enters the package of a later run (§4.7).

---

## Appendix A — Plan changes and precedence

**Amendments.** A frozen plan row that is weakened, changed, retargeted or removed requires a numbered entry `A-n` in `docs/VERIFICATION_PLAN.md` stating what changed, why, the alternative, and how the change was verified.

**Additions register.** Rows added during implementation are recorded in the same file, grouped by subsystem, one reason per group, each group stating whether it was written before or after the code it covers. A row added in response to a defect names the defect. No per-row justification.

The R-10 audit reads frozen rows, amendments and the register alike as plan rows.

**Precedence when sources disagree.**

1. The workbook outranks a layout note.
2. A Decision outranks the reference screenshot on data; the screenshot outranks a Decision on layout.
3. A measurement outranks all of them on a physical convention, including a value stated in this Annex.
4. Any other disagreement is reported, not resolved silently.
