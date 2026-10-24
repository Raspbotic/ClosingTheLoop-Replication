# Flight Simulator Replication Contract — Version 3

## 1. Scope

This document defines the standard of care (§2.1), the acceptance criteria (§3), the inputs
(§4), the prerequisites and the model-completeness process (§5), the non-functional constraints
(§6), the prompt sequence (§7), the implementation rules (§8), the gates (§9) and the outputs
(§10).

A run is accepted when gates G1–G12 pass. Acceptance is defined on the criteria of §3, not on
the text of the generated code.

The whole document is attached to the agent.

---

## 2. Definitions and conventions

| Term | Meaning |
|---|---|
| Agent | The LLM with a code-execution sandbox that executes the prompt sequence. |
| Practitioner | The person who assembles the package, issues the prompts, answers the gap report and checks the gates. |
| Sandbox | The agent's execution environment. Never evidence about the target. |
| Target | The host of §5.1, with the display characteristics of §5.6. |
| Model | The SysML v2 files of §4.1, including any addendum supplied under §5.3. |
| Workbook | The requirements workbook of §4.2. Authoritative for the requirements baseline. |
| **Functional** | Anything determining *what the system computes, exchanges, displays or decides*: message content, control laws, protocol behaviour, wire and loop rates, physical conventions, thresholds, seed data, units. |
| **Non-functional** | Anything determining *how the software is built, deployed, tested, observed or maintained* without changing what it computes. |
| Requirement id | Workbook form `L2SYS-REQ-nn.nn`. |
| Constraint id | `NFC-n`, §6. **Non-functional only.** A constraint never answers a functional question. |
| Gap | A functional question the model and the workbook do not answer. Recorded as `G-n`, §5.3. |
| **Observation** | Looking at what was built, in the state the operator will meet it. Not a test result about it. |

**Notation.** `> block quotes` are prompts, issued verbatim. *Italics* after a prompt is that
step's acceptance line.

**Provenance tags.** Every statement about the specification, a tool or a language carries one:

* **[a]** backed by a supplied artifact, cited — `[a: L2SYS-REQ-nn.nn]`, `[a: <model file>]` or
  `[a: <model file>#<element>]`, or `[a: NFC-n]`, **which may never be cited for a functional
  claim** (NFC-13).
* **[b]** derived from [a], naming what from.
* **[c]** the agent's own choice, flagged inline with what would settle it. A `[c]` on a
  functional matter names its `G-n`.

**Execution modes.** Either the practitioner issues prompts one at a time, or attaches this
document to one initiating prompt and the agent executes in order, reporting each acceptance
line. In the second mode the run **halts at R-1b** for the gap report unless §5.4's fallback is
authorized in writing. Mode, agent model identifier and elapsed time go in
`docs/RECREATION_LOG.md`.

---

## 2.1 Standard of care

These are not encouragements. Each names a corner that was cut in the V2 run or is routinely
cut in work of this kind, states the countermeasure, and — where one exists — how a reviewer
catches a violation. **A run that satisfies every gate while violating this section has failed.**

---

**I-1 — A count is not evidence.**
Never present a number of passing tests, files delivered or requirements covered as though it
demonstrated that the system works. Report what was **observed**, then the counts, in that
order. "366 tests pass" alongside an application whose main window does not fit the screen is
not a report; it is a distraction.
*Detection: any summary whose first claim is a number.*

---

**I-2 — Test the thing the way the operator gets it.**
No test may construct its subject in a manner the application does not. If the application is
responsible for creating a window, a thread or a connection, the test obtains it **from the
application**, never by calling the constructor itself. A test that builds what the application
forgot to build is worse than no test: it converts an absence into a green light.
*Detection: search the suite for direct construction of any component the application also
constructs; every such site must be justified in the test's docstring.*

---

**I-3 — Look at what you built.**
Anything whose acceptance is visual is **rendered and examined**, not merely asserted about.
Render every window the application shows, at the target's worst-case display size, read the
images back, and compare them against the reference artifact element by element. Deliver the
renders as evidence.
*Detection: `docs/OBSERVATION.md` exists and names every window, or it does not.*

---

**I-4 — Never substitute a weaker observable for the one stated.**
If a criterion says the application runs, running the test suite is not that. If it says a
window is displayed, a widget that paints correctly in isolation is not that. Where the stated
observable is genuinely unreachable — no target hardware, no display — say so **in the same
sentence as the claim**, name the substitute, and record it as an open item. Never report the
criterion met.
*Detection: every criterion reported met cites the observation that met it.*

---

**I-5 — Hunt for what would embarrass you.**
Before delivering, ask deliberately: *if the practitioner runs this for five minutes, what will
they find?* Write down the three most likely answers and check each. This is an active search,
not a duty to report what you happened to notice. Three defects that a five-minute run exposes
mean this search was not performed.
*Detection: `docs/RECREATION_LOG.md` carries the search, its candidates, and what checking each
one showed.*

---

**I-6 — Nothing is "done" that is partly done.**
A step whose acceptance line is partly met is reported as partly met, with the unmet half named.
A gate that cannot be run is reported unrun, never passed. A criterion discharged by proxy is
reported as discharged by proxy.

---

**I-7 — Simulate the target constraints you cannot execute.**
Not being able to run on the target is not a licence to ignore it. Its operating system, screen
geometry, display scaling, path conventions and interpreter versions are stated in this
document and in the model, and every one of them can be **modelled and asserted** from the
sandbox. An untested target constraint that was stated in writing is a defect, not a limitation.

---

**I-8 — A defect you find in your own work is reported as prominently as a success.**
It goes in the summary, not only in a document. Finding your own defect is the system working;
burying it is the failure.

---

**I-9 — Do not optimise for the checker.**
Where a criterion can be satisfied in a way that plainly does not achieve its intent, satisfy
the intent and report the discrepancy. If this document can be met by something obviously
inadequate, that is a defect in this document: report it under IR-7.

---

## 3. Acceptance criteria

1. **Architecture.** The process, thread and port architecture is identical to the TA.
2. **Traceability.** Every module carries [a]/[b]/[c] annotations; every cited requirement id
   exists in the workbook; every cited model element exists in the model; **no functional line
   cites an `NFC-n`**.
3. **Model authority.** This contract specifies no functional behaviour. Every functional value,
   rate, threshold, sign, unit, field name and law traces to the model, the workbook, or a
   numbered gap.
4. **Verification.** A pytest suite derived from the requirements before implementation passes
   headless and cites requirement ids. The number of tests is an outcome, never a target.
5. **Integration.** Every part the TA declares is **instantiated and reachable in the running
   application**, and every declared binding carries data in the assembled system at least once.
6. **Observation.** Every window the application shows has been rendered at the worst-case
   target display size, examined, and compared against the reference artifact, with every
   discrepancy listed.
7. **Fitness for the target display.** The application fits and remains operable within §5.6's
   worst case, asserted by a test.
8. **Measured conventions.** Every sign, unit and existence of an external library property is
   established by measurement, recorded with its procedure, and re-checked by the suite.
9. **Environment independence.** The application and the complete suite run to completion with
   no optional COTS tool installed and no optional hardware attached.
10. **Output hygiene.** A full suite run creates, modifies and deletes nothing outside
    `tmp_path`.
11. **Deployability.** Every pin is installable on the target interpreter and platform,
    established from package metadata.
12. **Gap closure.** Every functional question the model does not answer is a numbered `G-n`,
    answered in the model or carried as an open item. No gap is silently resolved.
13. **Model integrity.** The model, including any addendum, loads with zero errors in the SysML
    v2 Pilot Implementation.

Criteria 5, 6 and 7 exist because V2 had nothing like them and shipped three defects a
five-minute run exposed. **None of them can be met without launching the application.**

---

## 4. Package: inputs attached before R-0

### 4.1 SysML v2 model — 18 required, 1 optional, 1 conditional

| Layer | n | Files |
|---|---|---|
| Logical architecture, use cases | 2 | `fsim_LA.sysml`, `fsim_UCvsLA.sysml` |
| Technical architecture | 12 | `fsim_TA_Attributes.sysml`, `fsim_TA_Ports.sysml`, `fsim_TA_System.sysml`, `fsim_TA_PCS.sysml`, `fsim_TA_PCS_ThreadFlightSim.sysml`, `fsim_TA_PCS_ThreadFlightSim_UIM.sysml`, `fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml`, `fsim_TA_PCS_ThreadPhysics.sysml`, `fsim_TA_PCS_ThreadMapWindow.sysml`, `fsim_TA_Orch.sysml`, `fsim_TA_SEM.sysml`, `fsim_TA_Peripherals.sysml` |
| Requirements | 4 | `fsim_StakeholderRequirements.sysml`, `fsim_SystemRequirements.sysml`, `fsim_TA_IF_Requirements.sysml`, `fsim_R2F.sysml` |
| Optional | 1 | `fsim_Functions.sysml` |
| Conditional | 1 | `fsim_Addendum.sysml` — a previous run's answered gaps (§5.3). |

A `.txt` transport suffix is transparent; the agent restores the `.sysml` name.

**The model is the functional authority.** Where it is silent, §5.3 applies. Filling its silence
is not this contract's job.

### 4.2 Requirements workbook

`fsim_Design_v5.xlsx` or later. At v5: 34 L2 rows — 25 `T`, 2 `T, I`, 5 `I`, 2 `D`; 27 carry
`T`, 9 carry `I` or `D`. The agent reports the counts it finds and compares them against this
paragraph; a mismatch means a different workbook and is reported, not absorbed.

### 4.3 Reference GUI screenshots

`Desired_UIUX_capture.JPG`, `Desired_FGear_capture.JPG`.

**Authoritative for the appearance and arrangement of the user interface**, and they are
authoritative *as images*. The agent does not paraphrase them into a prose specification and
then implement the paraphrase — that is exactly what produced text labels where the image shows
instruments. The obligation is to **reproduce what the image shows**, and at R-9b to render the
result and compare it against the image element by element.

A screenshot is never authoritative for data, behaviour or units. Where it appears to state a
value, that is not a specification; if the model does not carry that value, it is a gap.

### 4.4 Legacy artifacts

`hotas_standalone.py`, and any other legacy source supplied.

**Evidence of what a previous implementation did, not a specification of what this one shall
do.** It may be read to *form* a proposed answer to a gap, with its basis stated. Where a legacy
artifact and the model disagree, the model governs and the disagreement is reported.

### 4.5 Reference environment manifests — inputs, not deliverables

`reference_venv_fsim_freeze.txt`, `reference_venv_hotas_freeze.txt`,
`reference_venv_fsim_wheels.txt`, `reference_venv_hotas_wheels.txt`. Captured on the target,
each environment's interpreter by explicit path:

```bat
.venv_fsim\Scripts\python.exe -m pip freeze > reference_venv_fsim_freeze.txt
.venv_fsim\Scripts\python.exe -c "import importlib.metadata as m,sys,platform;print(sys.version);print(platform.platform());[print(d.metadata['Name'],d.version,[l.split(':',1)[1].strip() for l in (d.read_text('WHEEL') or '').splitlines() if l.startswith('Tag:')]) for d in m.distributions()]" > reference_venv_fsim_wheels.txt
```

When none exists, the practitioner states so in writing and NFC-14 applies. Nothing in this
section appears in §10.

### 4.6 This document

### 4.7 Package check

```bash
grep -rl "test_" <package>/model/                            # expect: no matches
ls <package> | grep -E '^requirements_(main|hotas)\.txt$'    # expect: no matches
comm -12 <(ls <package>) <(§10 file list)                    # expect: empty
```

---

## 5. Prerequisites and the model-completeness process

### 5.1 Required

| Item | Requirement |
|---|---|
| Agent | LLM with a code-execution sandbox able to run Python, install packages, execute a suite, **and render a GUI offscreen to an image file it can read back**. |
| Target host, interpreters | As the model's runtime attributes state. Currently: Windows; Python 3.14 (`py -3.14`) and Python 3.11 (`py -3.11`). **From the model, not from this document.** |
| SysML v2 tooling | Pilot Implementation, on the practitioner's machine, for R-0b, R-1b and G1. |
| Network access | Once, to install dependencies. |
| Availability at R-1b | The practitioner answers the gap report, or authorizes §5.4 in writing. |

The rendering capability is **required**, not optional: without it criterion 6 cannot be met and
the run cannot be accepted.

### 5.2 Virtual environments

Neither exists on a fresh machine; both are created by `setup_venvs.bat` (R-3). Which subsystem
runs under which interpreter comes from the model's runtime attributes. Manifests are
`requirements_main.txt` and `requirements_hotas.txt`, pinned with `==` and validated per IR-6;
`requirements_main.txt` includes the test dependencies. No global `activate` anywhere (NFC-1).

### 5.3 The gap report

A **gap** is a functional question the implementation must answer that neither the model nor the
workbook answers. At R-1b the agent delivers `docs/MODEL_GAPS.md`, each entry carrying:

| Field | Content |
|---|---|
| `G-n` | Stable id, never reused. |
| Question | Stated so it has a determinate answer. |
| Requirement | The workbook row or model element it hangs off. |
| Why it blocks | What cannot be implemented, or must be guessed, without it. |
| Where the answer belongs | The model element that would carry it. **This is what makes the answer traceable next time.** |
| Proposed answer | Tagged `[c]`, with its basis, marked plainly as a proposal. |
| Consequence if wrong | What the system would do incorrectly, and which item would catch it. |

The practitioner answers **in the model**: by amending it, or by supplying `fsim_Addendum.sysml`
carrying the answers as model elements. There is no option that produces prose. An answer not in
the model is not an answer, because the next run cannot see it, the Pilot Implementation cannot
check it, and the audit cannot trace it.

### 5.4 Unattended fallback

If authorized in writing, the agent may proceed past R-1b. Then for every unanswered `G-n`: the
proposal is implemented and tagged `[c: G-n]` at every implementing line; the plan row covering
it is marked **provisional**; the run reports **conditionally complete**, never complete; and
G12 fails. The fallback labels and counts a guess. It does not close a gap.

### 5.5 Optional at runtime

| Item | Behaviour when absent |
|---|---|
| COTS visual system | The launch control reports it is not configured and does nothing. Everything else runs. |
| Aircraft visual package | The visual system starts with its default; a warning is issued. |
| Physical input device | The on-screen controls drive the aircraft. |

No COTS version number appears in the code or the tests (NFC-3).

### 5.6 Target display — the worst case

The application is operated on the target's display, not the agent's. Unless the practitioner
states otherwise in the initiating prompt, the worst case the delivery must satisfy is:

| Property | Worst case |
|---|---|
| Logical resolution available to a window | **1280 × 720** |
| Display scaling | **150 %**, and the application is scaling-aware |
| Chrome to allow for | title bar and a taskbar |

Every window must fit, and remain operable, inside that. NFC-11 states the obligation; R-9b
observes it; G11 gates it. If the practitioner's fleet is better than this, they may relax it in
writing — but not by silence.

---

## 6. Non-functional constraints

Cited as `[a: NFC-n]`. **None of them says what the system computes.** A functional statement
found here is a defect in this document, reportable under IR-7. Each declares a **scope**;
NFC-13 makes citation outside that scope an audit failure.

---

**NFC-1 — Runtimes and environments.** *Scope: environment scripts, manifests, launchers.*

One virtual environment per distinct interpreter the model requires, named
`.venv_<subsystem-group>`. `setup_venvs.bat` creates every environment, upgrades pip, installs
each pinned manifest, seeds the environment-variable file from its template, and reports what it
found. Idempotent; fails with a message naming a missing interpreter, not a traceback. **No
global `activate` anywhere**; every script and tool invokes its interpreter by explicit path. The
launcher activates one environment per window.

---

**NFC-2 — Dependency policy.** *Scope: imports, manifests.*

1. **No external middleware.** Inter-process transport uses the standard library.
2. **Standard library first.** A dependency is added only where the standard library cannot do
   the job, pinned with `==`, validated per IR-6.
3. **Tooling that must run before the environments exist uses the standard library only.**
4. **Lazy imports at environment boundaries.** A module that must import from an environment
   lacking one of its dependencies imports it inside the function that needs it, and the suite
   asserts the module imports without it.

---

**NFC-3 — COTS location and optionality.** *Scope: configuration, launchers, launch controls.*

No installation and no version of any COTS executable the model names is assumed.

* Location from the environment, variables named `FSIM_<TOOL>_*`: explicit executable path (used
  verbatim, highest precedence), install root, auxiliary directories.
* Resolution: explicit executable → install root → `PATH` → a scan of the platform's
  conventional install location, newest first by the version in the directory name → the bare
  program name, reported unresolved.
* Values seeded in an environment file the launchers `call` and the code parses directly; an
  exported variable beats the file.
* A missing executable blocks only the control that launches it and reports the variable to set.
  A missing auxiliary directory warns.
* **No COTS version number appears in the code or the tests.**

Which tools exist and which control launches them is functional, and comes from the model.

---

**NFC-4 — COTS invocation.** *Scope: the command line built for a third-party executable.*

A third-party tool's command-line syntax is a property of that tool and no supplied artifact
states it. The argument vector is built in exactly one place from named parts; every argument
whose *syntax* comes from the tool's documentation is tagged `[c]` with what would settle it;
every argument whose *value* is functional takes it from the model or an answered gap; optional
arguments appear only when set; and the launch never raises and never exits the application.

---

**NFC-5 — Measurement before use.** *Scope: every external library and every property of one.*

**The constraint that earned its place.** In the V2 run it found three inverted control
conventions and one bad initial condition, any of which would have flown the aircraft wrongly
under a green suite.

No sign, unit, range, default or existence of an external library's property, function or
behaviour is assumed — not from its name, not from a legacy artifact, not from this document,
not from the agent's prior knowledge. Before a property is read or written:

1. it is **measured** by executing the library in the sandbox — set a known input and observe
   the output; apply a command of known sign and observe which way the state moves; compare a
   value against its sibling in another unit;
2. the measurement, **the procedure that produced it**, and its observed output go in
   `docs/MEASUREMENTS.md`;
3. the measurement is cited at the implementing line;
4. the suite **re-measures** every convention the implementation depends on, so a library
   upgrade that reverses one fails the suite rather than changing behaviour silently.

Where a measured convention contradicts one the **model** fixes, the model governs this system's
interfaces and the implementation reconciles **at the boundary, in one place, with the
measurement cited** (IR-9). Where it contradicts nothing because the model is silent, that is a
gap, not a licence to choose.

The measurement procedure is delivered as a runnable tool.

---

**NFC-6 — Determinism and injectability.** *Scope: every module; the whole suite.*

Nothing that varies between machines is reachable except through a parameter: **clocks** (so a
rate is verified by counting events against a clock the test controls, never by sleeping);
**paths**; **environment and search path**; **filesystem and network** predicates; **devices**. A
default that reads the real machine is fine for the application; a test that relies on one is a
defect.

---

**NFC-7 — Trust boundaries.** *Scope: every ingress — sockets, files, user input.*

Data is validated **at the boundary**, field by field, against the model's constraints; a value
is not trusted because it parsed. Validation happens on ingress as well as on construction. A
rejected input raises **an error type belonging to this application**, not a standard-library
exception. A receive loop never dies on one bad input. A component specified as payload-opaque
does not decode what it relays.

---

**NFC-8 — Concurrency and shutdown.** *Scope: every thread, process and timer.*

Every spawned thread is stoppable through an explicit stop event and joined on shutdown.
Shutdown is safe on the target platform: a loop wakes on its own timeout and closes its own
resources rather than another thread closing a resource underneath it. No unhandled thread
exception in any suite run; the suite fails a row that leaks a non-daemon thread. Sockets that
must not block are non-blocking and the suite asserts it.

---

**NFC-9 — Test conventions.** *Scope: the suite and its runner.*

pytest; offscreen GUI. Tests bind the real ports the model assigns, and the "close running
instances" caveat is documented in the README and the runner. Long tests are marked `slow`.
Requirement ids are cited in test docstrings, or in the module docstring where a module verifies
one requirement throughout. Where a requirement names a UI element, the test drives the **real
widget** offscreen — a click or a value change on the named control — never an internal API.
Reference values are **not** read back from the implementation: a formula is re-derived in the
test as a hand computation with intermediates written out, a rate is measured, a wire value is
asserted against a literal transcribed from the model. The runner invokes the environment's
Python by explicit path and pauses on exit.

---

**NFC-10 — Integration completeness.** *Scope: the application's composition; the suite.*
**New in V3. Its absence is why a whole window went missing.**

1. **Every part the TA declares is instantiated by the application.** Where a TA part has a user
   interface, the application constructs it and makes it visible or reachable without further
   code.
2. **Every binding the TA declares carries data in the assembled application at least once**,
   asserted by a test that starts the application, not the component.
3. **A component test may not construct what the application is responsible for constructing**
   (I-2). Where a test needs the component, it obtains it from the application object.
4. The suite carries an **integration row per TA part**: start the application, assert the part
   exists, is wired to its declared peers, and receives what the model says it receives.

A green component test for a component the application never builds is the failure mode this
constraint exists to make impossible.

---

**NFC-11 — Fitness for the target display.** *Scope: every window and top-level widget.*
**New in V3.**

Within §5.6's worst case, every window the application shows:

* **fits** — width and height within the available logical area after chrome, with the
  application's own scaling awareness enabled;
* **remains operable** — no control is clipped, overlapped or pushed outside the viewport;
  content that cannot fit is reachable by scrolling, not lost;
* **degrades predictably** — the window opens no larger than the available area, and its
  position is on-screen.

The suite asserts the fit by measuring the assembled window's size requirements against §5.6's
figures. A layout whose minimum exceeds the worst case is a defect, not a preference.

---

**NFC-12 — Output hygiene.** *Scope: the suite; the application's default paths.*

A full suite run creates, modifies and deletes nothing outside `tmp_path`. The suite snapshots
the working tree at session start and compares it at the end. Every path the application writes
to by default is a parameter the suite can redirect.

---

**NFC-13 — Provenance and citation discipline.** *Scope: every line of code and prose.*

1. Exactly one tag per statement about the specification, a tool or a language.
2. An `[a]` citation names a workbook requirement, a model element, or an `NFC-n`.
3. **An `NFC-n` may never be cited for a functional claim.** A line that computes a command,
   builds a message or decides a threshold and cites an `NFC-n` is citing the wrong kind of
   authority; each constraint's declared scope makes this checkable.
4. A `[c]` on a functional matter names its `G-n`. One without is an unrecorded decision — the
   thing this version exists to prevent.
5. An unsupported statement is `[c]` with what would settle it. Never an unmarked assertion.

---

**NFC-14 — No reference environment.** *Scope: the manifests.*

With no §4.5 manifests and a written statement to that effect, the agent pins the newest release
of each package satisfying IR-6, records every pin `[c]`, and raises an open item naming
`pip freeze` on the target as what would settle it. It does not pin from what its sandbox has.

---

### 6.1 Deliberately absent

For a reader holding V2, the following are **not** here and must be in the model: message
schemas, field lists and ranges; protocol behaviour and timings; port numbers; wire formats with
their versions, sizes and byte order; endpoint paths; aircraft and visual model identities;
input-to-surface mapping; button functions; ground, engine and fuel semantics; terrain rates and
slew limits; input precedence rules and windows; control-law structure, gains and limits; the
radar equation, its parameters and defaults; airfield seed data; telemetry field lists; airspeed
and engine-state semantics; alerting conditions, thresholds and cadences; map projection
parameters, zoom limits and symbology. Appendix C maps each to its V2 Decision.

---

## 7. Prompt sequence

### R-0 — Role and rules

> Role: software developer of this project. Objective: develop a software application that
> complies with the attached design specification (SysML v2 model and requirements workbook).
>
> Rules:
> 1. **The model and the workbook are the sole functional authority.** This contract specifies
>    no functional behaviour. Where the model is silent on something you need, you do not decide
>    it — you record it as a gap at R-1b.
> 2. All generated code is traceable: [a] backed by a supplied artifact, citing a requirement
>    id, a model element, or a non-functional constraint NFC-n; [b] derived, naming what from;
>    [c] own choice, flagged inline with what would settle it, naming its G-n where functional.
> 3. An NFC-n may never be cited for a functional claim.
> 4. §2.1 (standard of care), §6 and §8 hold throughout the run and outrank convenience.
> 5. Assess each prompt for feasibility within your context and output limits; propose a step
>    breakdown if needed.
> 6. Verify your outputs by executing them in your sandbox before delivering them. Where an
>    output is visual, render it and look at it.
> 7. Deliver files, not fragments; name the files a fix touches; never paste diff notation into
>    a source file.
>
> Target: as the model's runtime attributes state, with none of the optional COTS tools and no
> optional hardware installed, no virtual environments, and the display worst case of §5.6. Your
> sandbox is not the target and is never evidence about it.
>
> List every attached file and state what each contributes. State your model identifier.

*Acceptance: the inventory names the 18 required model files, the workbook, both screenshots,
every legacy artifact, any addendum, and either the four §4.5 manifests or a statement that they
were not supplied. Any unreadable file reported here.*

### R-0b — Model intake

> Read every attached `.sysml` file end to end. Deliver `docs/MODEL_INTAKE.md`: a one-line
> summary of each file; a table of file, declared package, and any package declared by more than
> one file; and, for each attached screenshot, **a description of what it shows, element by
> element** — this is the specification of the user interface and you will be held to it at
> R-9b.
>
> State whether you have a SysML v2 parser, having checked rather than assumed; report what you
> ran. Without one, every claim about SysML v2 syntax is [c] unless it quotes a supplied file,
> and the intake states which classes of error your reading cannot detect.

The practitioner loads the model into the Pilot Implementation and supplies any problem list as
data. With no parser and no list the agent proceeds from R-1, tags syntax claims [c], and raises
an open item that G1 is unrun.

*Acceptance: every model file summarized; duplicate package declarations reported; parser
availability stated with its evidence; every screenshot described element by element.*

### R-1 — Architecture

> Derive the process architecture **from the model**: processes, threads per the TA thread
> decomposition, and every inter-process link with its port number from the TA. Cross-check each
> port across the TA files and against the interface requirements. Report inconsistencies; do
> not resolve them.
>
> Deliver `docs/ARCHITECTURE.md` with a link table (source, sink, port, protocol, rate), the
> planned file tree, and **a list of every TA part that has a user interface, naming the window
> or panel that will present it.** Every cell cites the model element it came from. **A cell you
> cannot fill from the model is left empty and becomes a gap at R-1b.**

*Acceptance: every port number cited to a model element; processes and threads match the TA;
every UI-bearing TA part is named with the window that will present it; inconsistencies
reported, not resolved; unfillable cells left empty.*

### R-1b — Model completeness report

> Deliver `docs/MODEL_GAPS.md` per §5.3. Work the workbook row by row: for each requirement,
> state what the model gives you and what it does not. A requirement implementable entirely from
> the model is recorded as such in one line — that list is as informative as the gap list.
>
> Where a legacy artifact suggests an answer you may propose it, saying its basis is a legacy
> artifact and not a specification. Where a measurement can settle a question, take it and
> record it under NFC-5 — a question settled by measurement is not a gap.
>
> Implement nothing in this step.

*Acceptance: every `T` row is either fully model-covered or has at least one `G-n`; every `G-n`
names where in the model its answer belongs; no gap resolved by the agent's own authority;
nothing implemented.*

### R-2 — Verification plan

> From the workbook extract every L2 requirement and its V-Method; report the counts. For each
> `T` row derive a specification from **the requirement statement and the model** alone:
> stimulus, observation point, pass criterion, and reference values computed independently of
> the future implementation.
>
> Include, as frozen rows: an **integration row per TA part** (NFC-10) that starts the
> application and asserts the part exists and is wired; a **fit row per window** (NFC-11) against
> §5.6; hermeticity rows; and conformance rows for the model's interface definitions. A row
> depending on an unanswered `G-n` is marked **provisional** and names it.
>
> For each `I`/`D` row produce a manual instruction with a stable id `M-n`.
>
> Deliver `docs/VERIFICATION_PLAN.md`. The plan is frozen before implementation. A frozen row may
> be weakened, changed or removed only by a numbered amendment `A-n` stating what changed, why,
> the alternative, and how the change was verified. Added rows go in an Additions register,
> grouped by subsystem, one reason per group, each stating whether it was written before or
> after the code it covers.

*Acceptance: every `T` row has a specification; every specification cites the model element or
`G-n` its pass criterion rests on; the integration and fit rows exist and are frozen; every
`I`/`D` row has an `M-n`; the counts match §4.2.*

### R-3 — Runtime environments and common layer

> Deliver `requirements_main.txt` and `requirements_hotas.txt` per §5.2; `tools/check_wheels.py`
> confirming from package metadata that every pin is installable on the stated target, reporting
> its output for both; and `setup_venvs.bat` per NFC-1.
>
> Then implement the common layer the TA's shared elements require: port constants each cited to
> the TA element stating it, the message types the model's interface definitions define, and the
> COTS-location policy of NFC-3. Create `tests/` with a conftest satisfying NFC-6, NFC-9 and
> NFC-12, and a first module asserting the port constants against their model elements, message
> round-trips, malformed-input rejection per NFC-7, and the NFC-3 resolution rows.

*Acceptance: every pin reported installable on the target; every port constant asserted against a
model element, not against this contract; on a machine with only the required interpreters,
`setup_venvs.bat` then the suite is green with no manual step between.*

### R-4 … R-8 — Subsystem implementation

> Implement `<module>` against `<the TA part and its actions>` and the requirements the R2F
> matrix allocates to it. Take every functional value, rate, threshold, sign, unit and field name
> from the model or an answered `G-n`, and cite each at its implementing line. Apply NFC-5 to
> every external library property you touch. Implement the plan rows allocated to this step,
> **including its integration row**.
>
> Where the requirement names a UI element, the test drives the real widget offscreen, obtained
> from the application (NFC-10.3). Where the screenshot shows how an element looks, reproduce
> what the image shows, not a description of it.
>
> If you find a functional question neither the model nor an answered gap covers, **stop and add
> a `G-n`** rather than deciding it.

| Step | Subsystem | TA source |
|---|---|---|
| R-4 | Peripheral input transmitter | `fsim_TA_Peripherals.sysml` |
| R-5 | Physics thread and initial-condition data | `fsim_TA_PCS_ThreadPhysics.sysml` |
| R-6 | Bus orchestrator | `fsim_TA_Orch.sysml` |
| R-7 | Synthetic environment and tactical map | `fsim_TA_SEM.sysml`, `fsim_TA_PCS_ThreadMapWindow.sysml` |
| R-8 | Pilot Control Station | `fsim_TA_PCS_ThreadFlightSim*.sysml` |

*Acceptance, each step: the applicable plan rows green with no optional COTS tool and no optional
hardware; the step's integration row green; every functional constant cites a model element or an
answered `G-n`; every external-library property has a measurement with its procedure; any new gap
reported, not decided.*

### R-9 — Integration deliverables

> Deliver the launcher per NFC-1, the runner per NFC-9, the environment-file template per NFC-3,
> and an end-to-end test: every process the TA declares, in one process space, over the real
> ports, asserting each link of the R-1 table carries what the model says it carries (marked
> `slow`). Deliver `README.md` with a quick-start that works with only the required interpreters.

*Acceptance: the full suite green headless; a second run green; green with and without each
optional COTS tool and with no optional hardware, identical counts; the working tree unchanged.*

### R-9b — Observation

> **Do not report this run complete until this step is done.**
>
> Launch the assembled application the way the launcher launches it, headless, with the display
> constrained to §5.6's worst case. Then:
>
> 1. Enumerate every top-level window it creates. Compare that list against R-1's list of
>    UI-bearing TA parts. **A part on that list with no window is a defect; name it.**
> 2. Render every window to an image file, **read the images back, and look at them.**
> 3. Compare each render against the reference screenshot **element by element**, using your own
>    R-0b description as the checklist: for every element the image shows, state whether the
>    application shows it, and if so whether it looks like the reference or merely carries the
>    same name.
> 4. Measure each window against §5.6: minimum size, preferred size, and whether every control
>    is inside the viewport at the worst case.
> 5. Exercise the primary operator path — start the simulation, move a control, observe the
>    displays change — and render again.
>
> Deliver `docs/OBSERVATION.md`: the window inventory, the renders, the element-by-element
> comparison, the fit measurements, and **a list of every discrepancy found, with none omitted
> because it is small or awkward**. Deliver the renders themselves.
>
> Then apply I-5: name the three most likely ways a five-minute run by the practitioner would
> embarrass this delivery, and check each. Record the search and its results.

*Acceptance: every UI-bearing TA part has a window; every window is rendered, read back and
compared element by element against the reference; every window fits §5.6's worst case; every
discrepancy is listed; the I-5 search is recorded with what checking each candidate showed.*

**This step exists because the V2 run had all the capability to do it and did not.** The three
defects that reached the practitioner would each have been caught by point 1, point 3 and point 4
respectively.

### R-10 — Closure audit

> Deliver `tools/closure_audit.py`, checking:
> 1. every `L2SYS-REQ-*` id cited in the source or tests exists in the workbook;
> 2. every model element cited exists in the model;
> 3. every workbook `T` row is cited by a test docstring, or by a module docstring where the
>    module verifies one requirement throughout;
> 4. every implemented test maps to a frozen row, an amendment, or the Additions register, and no
>    frozen row was removed without an amendment;
> 5. every port constant matches the TA, re-derived from the model files at audit time;
> 6. every `NFC-n` citation lies within that constraint's declared scope, and no functional `[c]`
>    lacks a `G-n`;
> 7. **every TA part has an integration row and that row exists in the suite** (NFC-10);
> 8. **every window named in `docs/OBSERVATION.md` has a render and a fit measurement** (NFC-11);
> 9. every `G-n` is answered in the model or listed outstanding;
> 10. every workbook `I`/`D` row has a recorded, attributed result in `docs/MANUAL_RESULTS.md`;
> 11. every module carries [a]/[b]/[c]; the census goes to `docs/PROVENANCE.md` with counts and
>     shares, citation targets **broken down by kind — requirement, model element, constraint** —
>     and the full inventory of every [c] with its `G-n`, stating that counts are tag occurrences
>     and the classification is self-reported.
>
> The audit's evidence is the source files, the model, the workbook, the plan's rows and the
> observation record. Demonstrate on a fixture that it neither credits nor charges an item that
> is only illustrative — including a result row inside a formatting example.
>
> Output the eleven lists and the test count (functions and pytest items, separately). Checks 9
> and 10 report OPEN by default; `--strict-gaps` and `--strict-manual` make them fail.

*Acceptance: checks 1–8 and 11 empty or complete; the suite green; checks 9 and 10 list their
outstanding rows as OPEN; the fixture demonstration passes.*

### R-11 — Hardware verification

The practitioner's, on the target hardware. Items `HW-n` in `docs/MANUAL_VERIFICATION.md`,
derived from the model's peripheral and visual-system elements and the `I`/`D` rows.

### R-12 — Manual verification record

> Deliver `docs/MANUAL_VERIFICATION.md` (one `M-n` per workbook `I`/`D` row, plus `HW-n`) and
> `docs/MANUAL_RESULTS.md` (Id / Req / Date / Tester / Build / Result / Evidence, one row per item
> per run). `Result` ∈ {PASS, FAIL, BLOCKED, NOT RUN, N/A}. **A PASS without a date and a tester
> is invalid.** A re-run adds a row; earlier rows are not edited.

*Acceptance: `closure_audit.py --strict-manual` passes only when every `I`/`D` row carries an
attributed PASS.*

---

## 8. Implementation rules

| Id | Rule |
|---|---|
| IR-1 | Tag every statement about the specification, a tool or a language; an unsupported one is [c] with what would settle it. |
| IR-2 | Establish every claim about the execution environment by running a command and recording its output; an unexecuted environment claim is a defect, not a [c]. |
| IR-3 | With no parser and no problem list, proceed under R-0b's fallback; never infer SysML v2 syntax rules. |
| IR-4 | Test any check whose behaviour differs between sandbox and target in both states, and negative-check it by reintroducing the defect. |
| IR-5 | Reference requirements by their stable alias, never by a name derived from the requirement text. |
| IR-6 | Validate every pin against the target interpreter and platform tag from package metadata, never from a successful sandbox install. Accept pure-python, version-agnostic-platform, stable-ABI-at-or-below-target and exact-tag wheels; reject a free-threaded-only tag. |
| IR-7 | **Report, never resolve.** Where two supplied artifacts disagree, where the model is silent, or where this contract appears to state something functional or to be satisfiable by something inadequate — report it and do not choose. |
| IR-8 | A functional constant is defined once, where the model's element maps to code, and cited there. A value appearing twice will eventually be two values. |
| IR-9 | A reconciliation between a measured external convention and a model-defined one happens in exactly one place, named in `docs/MEASUREMENTS.md`. |
| IR-10 | **A component is wired into the application in the same step that creates it.** Creating a component and deferring its integration is how a component ends up finished, tested and never used. |

---

## 9. Acceptance gates

| Gate | Command | Pass condition | Criterion |
|---|---|---|---|
| G1 | Load the full model, with any addendum, in the Pilot Implementation | problem list empty | 13 |
| G2 | On a host with only the required interpreters: `setup_venvs.bat` then `run_tests.bat` | both succeed, no manual step between | 9, 11 |
| G3 | `run_tests.bat` twice after the last edit | green both times | 4 |
| G4 | `run_tests.bat` with no optional COTS tool and no optional hardware, then with a tool-shaped installation present | green, identical counts | 9 |
| G5 | `closure_audit.py` | checks 1–8 and 11 empty or complete | 1, 2, 5, 6 |
| G6 | `closure_audit.py` check 5, and read the port constants | every port matches the TA | 1 |
| G7 | `closure_audit.py` check 6 | no `NFC-n` outside scope; no functional `[c]` without a `G-n` | 2, 3 |
| **G8** | **Read `docs/OBSERVATION.md` and open the delivered renders** | **every UI-bearing TA part has a window; every window rendered and compared element by element; every discrepancy listed** | **5, 6** |
| **G9** | **`closure_audit.py` checks 7 and 8** | **every TA part has an integration row; every window has a render and a fit measurement** | **5, 6** |
| **G10** | **Run the application on the target and use it for five minutes** | **nothing is found that `docs/OBSERVATION.md` does not already list** | **5, 6, 7** |
| G11 | The fit rows of the suite | every window fits and stays operable at §5.6's worst case | 7 |
| G12 | `closure_audit.py --strict-gaps` | exit 0 — every `G-n` answered in the model | 3, 12 |
| G13 | Read the transcript against `docs/VERIFICATION_PLAN.md` | the plan predates R-3; no frozen row weakened or removed without an amendment | 4 |
| G14 | `git status --porcelain` after a full suite run | empty | 10 |
| G15 | `closure_audit.py --strict-manual` | exit 0 | manual rows |

**G10 is the gate V2 lacked and the one the practitioner actually applied.** It is deliberately
informal and deliberately last: it asks whether the delivery survives contact with a person
using it. A run whose G10 turns up something not already in `docs/OBSERVATION.md` has failed
R-9b, whatever the other gates say.

A run passing G1–G11, G13 and G14 is **code-complete against criteria 1–11**. G12 closes model
authority; without it the run is **conditionally complete**. G15 closes verification. G1, G2 and
G10 are the practitioner's; an agent that cannot run them records an open item and reports them
neither as passed nor as blocking.

---

## 10. Outputs of a run

```
flightsim/                    application package, decomposed per R-1
tests/                        conftest and one module per subsystem
tools/closure_audit.py        R-10
tools/check_wheels.py         R-3
tools/measure_*.py            R-4..R-8, the NFC-5 measurement procedures
docs/ARCHITECTURE.md          R-1, every cell cited to the model
docs/MODEL_INTAKE.md          R-0b, including the screenshot descriptions
docs/MODEL_GAPS.md            R-1b
docs/VERIFICATION_PLAN.md     R-2, frozen, amendments + Additions register
docs/MEASUREMENTS.md          NFC-5
docs/OBSERVATION.md           R-9b, with the renders it references
docs/observation/*.png        R-9b, the renders themselves
docs/MANUAL_VERIFICATION.md   R-12 procedure
docs/MANUAL_RESULTS.md        R-12 record
docs/CLOSURE_AUDIT.md         R-10 output
docs/PROVENANCE.md            R-10 check 11
docs/RECREATION_LOG.md        mode, agent model identifier, prompts issued outside
                              this document, elapsed time, gap round-trips, defect
                              rounds, the I-5 search, observations, open items
setup_venvs.bat  requirements_main.txt  requirements_hotas.txt          R-3
launch_flightsim.bat  run_tests.bat  fsim_env.bat.template  README.md   R-9
```

`docs/RECREATION_LOG.md`, `docs/MODEL_GAPS.md` and `docs/OBSERVATION.md` are mandatory. No file
here enters a later run's package (§4.7) except `fsim_Addendum.sysml`, which is the
practitioner's output and enters as model.

**The final report to the practitioner opens with what was observed, not with a count (I-1).**

---

## Appendix A — Plan changes and precedence

**Amendments.** A frozen row weakened, changed, retargeted or removed requires a numbered `A-n`
stating what changed, why, the alternative, and how the change was verified.

**Additions register.** Rows added during implementation, grouped by subsystem, one reason per
group, each stating whether written before or after the code it covers. A row added for a defect
names the defect.

**Precedence.** V2 needed four rules because its Annex could contradict the model. Three suffice:

1. **On any functional matter the model and the workbook govern.** Nothing else — not this
   contract, not a screenshot, not a legacy artifact, not convention. Where the workbook and the
   model disagree, the workbook governs the requirement and the disagreement is reported.
2. **On the appearance of the user interface the reference screenshots govern**, as images.
   They never govern data, behaviour or units.
3. **On a physical convention of an external library, measurement governs** — over the agent's
   expectations and over any legacy artifact. Where it meets a model-defined convention, the
   model governs this system's interfaces and the reconciliation happens in one place.

Any other disagreement is reported, not resolved (IR-7).

---

## Appendix B — The V2 post-mortem, and which clause each defect produced

Kept because a rule whose origin is visible is harder to skip.

| Defect found by the practitioner | Pattern | Clause added |
|---|---|---|
| The tactical map window was never displayed. The agent implemented every TA binding except the one that puts pixels on a screen, and wrote a component test that **constructed the widget itself** — a green light for a window the application never created. | Testing the part, never the whole; deferring integration. | **I-2**, **NFC-10**, **IR-10**, R-9b.1, audit check 7, **G9** |
| Cockpit instruments were text labels where the reference screenshot shows round dials. The agent had read the screenshot and described it accurately in prose, then implemented the Annex's easier prose restatement. | Satisfying a paraphrase of an authority instead of the authority. | **§4.3** rewritten, removal of the layout Decision, R-0b screenshot description, R-9b.3, **G8** |
| The main window did not fit the monitor. `sizeHint` was 1248×819 logical, which at Windows' default scaling exceeds common displays; nothing clamped it to the screen. | A stated target constraint never modelled from the sandbox. | **I-7**, **NFC-11**, **§5.6**, R-9b.4, **G11** |
| 366 passing tests reported alongside all three. | A count offered as evidence. | **I-1**, **I-5**, R-9b tail, **G10** |

---

## Appendix C — Migration from V2

Seventeen functional Decisions must be carried by the model before a V3 run reaches G12.

| V2 | Subject | Class | V3 disposition |
|---|---|---|---|
| D1 | Wire format, message types, ranges, error type | functional + NF tail | Types, fields, ranges → **model**. "Own error type, validate on decode" → **NFC-7**. |
| D2 | Beacon protocol, refresh and staleness | **functional** | → **model**. *Caused amendment A-2.* |
| D3 | Orchestrator: relay, no middleware, two nodes, opaque, non-blocking | mixed | "No external middleware" → **NFC-2**. "Non-blocking" is already a requirement. Node structure and ports → already **model**. |
| D4 | Port map; wire version/size/byte order; endpoint paths | **functional** | Ports → **already in the model, three places each**; D4 was redundant. Wire details → **model**. *Its partial specification caused an open item.* |
| D5 | Two runtimes, venvs, no global activate | mixed | Interpreter assignment → already **model**. Venv creation and explicit paths → **NFC-1**. |
| D6 | Physics and visual model identities; launch command | mixed | Identities → **model**. Command-line syntax → **NFC-4**. |
| D7 | Button map, dual routing, axis selection | **functional** | → **model**. |
| D8 | Ground and engine semantics | **functional** | → **model**. *Caused amendment A-1.* |
| D9 | Terrain rate, slew limit, failure tolerance | **functional** | → **model**. |
| D10 | Input precedence window and behaviours; throttle scale | functional + layout | Window, centring, retention, mirror rate → **model**. Scale marks → **screenshot**. |
| D11 | Autopilot laws, gains, limits | **functional** | → **model**. *Caused amendment A-3.* |
| D12 | Radar equation, parameters, defaults | **functional** | → **model**, on the action that already exists for it. |
| D13 | Airfield seed data and override semantics | **functional** | → **model**, or a data file the model names. |
| D14 | PCS layout; telemetry field list | mixed | **Layout → the screenshot, as an image (§4.3). Its prose restatement is what produced text labels instead of instruments.** Field list → **model**. |
| D15 | Test conventions | non-functional | → **NFC-9**. |
| D16 | COTS location and optionality | non-functional | → **NFC-3**, generalized. |
| D17 | Measure library properties before wiring | non-functional | → **NFC-5**, strengthened. **The most valuable clause in V2.** |
| D18 | Test hermeticity | non-functional | → **NFC-12**, with NFC-6. |
| D19 | State field list and units | **functional** | → **model**. |
| D20 | Airspeed convention | **functional** | → **model**. |
| D21 | Engine state semantics | **functional** | → **model**. |
| D22 | No reference environment fallback | non-functional | → **NFC-14**. |
| D23 | Alerting condition and cadence | **functional** | → **model**. |
| D24 | Map projection, zoom limits, symbology | mixed | Projection, limits, caching → **model**. Symbology → **screenshot**. |

**New in V3 with no V2 ancestor:** §2.1 in full; §5.6; NFC-6, NFC-10, NFC-11, NFC-13; IR-7,
IR-8, IR-9, IR-10; the R-1b gap report; the R-9b observation step; audit checks 6, 7, 8 and 9;
and gates G7, G8, G9, G10, G11 and G12.

---

## Appendix D — What V3 does not fix

**Removing functional decisions does not make the model complete.** Run against the V2-era model
unchanged, R-1b will produce a long gap report — plausibly thirty entries or more — and every one
is a question V2 answered in prose and V3 declines to answer. That is intended, not a regression:
the questions were always there. V2 answered them where the model could not see them, the audit
could not check them and the next run inherited them only if someone remembered to attach the same
annex. The work of answering them is real, it is the practitioner's, and doing it once into the
model retires it permanently.

**Nothing here can make an agent care.** §2.1 raises the cost of cutting a corner and adds
detection for the specific corners cut in the V2 run. It cannot enumerate every corner. The V2
run passed every gate it was given while shipping three defects a five-minute session exposed —
so the honest statement is that the gates were the ceiling of what was checked, not the floor of
what was required. G10 is in this document precisely because it is the one check that does not
decompose into a procedure an agent can satisfy narrowly: a person uses the software and says
whether it works.
