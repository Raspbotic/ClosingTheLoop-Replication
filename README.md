# ClosingTheLoop-Replication

Replication package for the article

> G. Marsiglia, "Regenerating a Cyber-Physical System with a Large Language Model under a SysML v2-Based Execution Contract," submitted to *Systems Engineering* (Wiley/INCOSE), 2026.

Author: Gianluca Marsiglia, independent researcher, Ingolstadt, Germany. ORCID https://orcid.org/0009-0007-0260-0041.
Archived version: Zenodo DOI to be added at acceptance. Please cite the article and this package as given in `CITATION.cff`.

The article reports a single-author case study in which a SysML v2 model, an annex of design decisions and a written procedure, together the *execution contract*, serve as the specification from which an LLM agent regenerates a distributed flight-simulator prototype, code and tests, over four contract versions. This package contains everything Section 6.5 of the article lists, plus the generated builds and their run documentation, so that every figure in Tables 1 to 3 can be traced to the file it was taken from.

## Contents

| Folder | Contents |
|---|---|
| `1_contract/` | The four execution contracts, `REPLICATION_CONTRACT_V1.md` to `_V4.md`, as handed to the agent (version 4 is the form described in Section 4 of the article), and `DECISIONS_TO_BEHAVIORS.md`, the mapping of the 24 decisions of version 2 to the 15 behaviours of version 4 (Section 5.2). |
| `2_model/` | The SysML v2 model as handed to version 4: the 18 required files plus the optional `fsim_Functions.sysml` (19 files; see "Model baseline" below). The verification matrix `fsim_RVM.sysml` is included without verification evidence, as the contracts prescribe. |
| `3_requirements/` | The requirements workbook `fsim_Design_v5.xlsx`, from which the requirement layer, the allocation matrices and the verification matrix of the model are generated (Section 5.1). |
| `4_references/` | The two reference screenshots (`Desired_UIUX_capture.JPG`, `Desired_FGear_capture.JPG`), authoritative for the appearance of the user interface, and `hotas_standalone.py`, the transmitter script of Section 5.1, declared in the package as an input that fixes conventions. |
| `5_virtual_environments/` | The pinned Python requirements files of version 4 (`requirements_main.txt` for Python 3.14, `requirements_hotas.txt` for Python 3.11) and the setup script. These manifests also run the version-1 and version-2 builds. |
| `6_generated_code/` | One folder per build: `reference/`, `v1/`, `v2/`, `v4/`. Each holds the application package, the test suite and the run documentation (`docs/`) as consolidated, that is, after the consolidation run of Figure 1. Version 3 has no folder: the agent reported the gaps and halted before writing code (Section 5.3); its session log and gap list are in `6_generated_code/v3_session/`. |

Nothing in this package was edited after the runs except the redactions noted below.

## Checklist against Section 6.5 of the article

| Section 6.5 item | Where |
|---|---|
| The four contract texts | `1_contract/` |
| The model with verification evidence removed | `2_model/` |
| The requirements workbook | `3_requirements/` |
| The reference screenshots | `4_references/` |
| The transmitter script | `4_references/hotas_standalone.py` |
| The mapping from the 24 decisions to the 15 behaviours | `1_contract/DECISIONS_TO_BEHAVIORS.md` |
| The Python requirements files of each virtual environment | `5_virtual_environments/` |
| Delivered applications, suites, audit outputs, censuses, session logs | `6_generated_code/<build>/` |

## Where each figure of the article comes from

| Article | Backing file |
|---|---|
| Table 1, acceptance criteria, gates, rules, lengths per version | `1_contract/REPLICATION_CONTRACT_V*.md` |
| Table 2, prompts and durations | `6_generated_code/<build>/docs/RECREATION_LOG.md` (v1, v2) or `RUN_RECORD.md` (v4), and the session logs |
| Table 2, tests (pytest items): 56 / 241 / 366 / 97 | `docs/CLOSURE_AUDIT.md` of each build (v1 also reports 208 test functions; v2 273; v4 88) |
| Table 2, code size (lines) | counted over the application and test files of each build folder |
| Table 2, defects before delivery | the amendments of `docs/VERIFICATION_PLAN.md` (v1, v2) and `docs/OBSERVATION.md` (v4) |
| Table 2, defects after delivery | the post-delivery amendments and the Additions register of `docs/VERIFICATION_PLAN.md` (v1: A-6 to A-10; v4: "field report" groups) |
| Table 2, requirements covered 27/27 and audit violations 0 | `docs/CLOSURE_AUDIT.md` of each build |
| Table 2, provenance census: 707 / 678 / 260 tags | `docs/PROVENANCE.md` (v1, v2); for v4 the census line of `docs/CLOSURE_AUDIT.md` ([a] 200, [b] 17, [c] 43) |
| Table 3, defects by kind | the same amendment and field-report records, classified by the author |
| Section 5.5, defects and gaps reported to the model | `docs/MODEL_INTAKE.md` (v1, v2), `docs/R0_INVENTORY.md` findings F2, F5 and F11 (v4), the v3 gap list |
| Section 5.2, observation step of version 4 | `6_generated_code/v4/docs/OBSERVATION.md` and the renders in `observation/` |

The census counts tag occurrences in the application code (`flightsim/`), self-reported by the agent; the article normalizes them by the lines of application and test code, in the same way for every version. The reference build, generated before the tag instruction existed, was tagged and counted by hand afterwards.

## Run documentation: file names differ by version

The contracts of versions 1 and 2 fixed the names of the output documents; version 4 named only the prompt deliverables, so its agent named the files after the prompts. The documents correspond as follows.

| Content | v1, v2 | v4 |
|---|---|---|
| Intake of the specification | `MODEL_INTAKE.md` | `R0_INVENTORY.md` |
| Architecture memo and link table | `ARCHITECTURE.md` | `R1_ARCHITECTURE.md` |
| Verification plan, amendments, additions | `VERIFICATION_PLAN.md` | `R2_VERIFICATION_PLAN.md` |
| Measured conventions of external libraries | `FDM_MEASUREMENTS.md` | `MEASUREMENTS.md` |
| Observation of the assembled application | — (not in the contract) | `OBSERVATION.md` |
| Closure audit | `CLOSURE_AUDIT.md` | `CLOSURE_AUDIT.md` (includes the census line) |
| Provenance census | `PROVENANCE.md` | in `CLOSURE_AUDIT.md` |
| Manual verification procedure and results | `MANUAL_VERIFICATION.md`, `MANUAL_RESULTS.md` | `MANUAL_VERIFICATION.md` |
| Run record (mode, model identifier, timing, prompts outside the contract) | `RECREATION_LOG.md` (written by the agent) | `RUN_RECORD.md` (written by the author from the session log; the version-4 contract did not ask the agent for one) |

## Running version 4

See `6_generated_code/v4/README.md` for the quick start written by the agent. In short: Windows, Python 3.14 and 3.11 installed; run the setup script of `5_virtual_environments/` once; `run_tests.bat` runs the suite headless; `launch_flightsim.bat` starts the orchestrator, the synthetic environment manager, the pilot control station and the HOTAS transmitter in demo mode. FlightGear and a physical HOTAS are optional; without them the launch button reports that the visual system is not configured and the virtual HOTAS flies the aircraft. Map tiles need network access to the OpenStreetMap tile service.

## Notes and known differences

- **Model baseline.** `2_model/` holds the model as handed to version 4 (the baseline of Table 1 for versions 3 and 4). Versions 1 and 2 ran on earlier revisions of the same files; the corrections between them are those reported in Section 5.5, and the intake documents of each build describe what that build received. The earlier revisions are not shipped separately.
- **Workbook.** Version 1 was run against `fsim_Design_v4.xlsx` (see its verification plan); versions 2 to 4 against `fsim_Design_v5.xlsx`, which is the one shipped. The requirement set (34 L2 rows, 27 marked T) is the same in both.
- **Environments.** Only the version-4 manifests are shipped; they are sufficient to run the version-1 and version-2 builds as well.
- **Version 3.** No code was produced; the folder holds the session log and the agent's gap list only.
- **Test counts.** Table 2 reports pytest items (parametrized rows expanded). The audits also report test functions, which is a smaller number.
- **Defects after delivery, version 1.** Table 2 counts three product defects for version 1. The verification plan's post-delivery amendments A-6 to A-10 describe more items than three, because they also record test-only defects and verification gaps; the three counted are [to be named by the author].
- **Session logs** were exported from the agent's interface and redacted of local paths, user names, machine names and keys; redactions are marked `[redacted]`.
- **Nineteen model files.** The contracts count 18 required files; `fsim_Functions.sysml` is optional and was supplied, hence 19 in the folder.

## Licenses

Software (the generated applications and tests, scripts, manifests, the transmitter script): MIT, see `LICENSE`. Documentation and specification artifacts (contracts, model, workbook, screenshots, run documentation): CC BY 4.0, see `LICENSE-DOCS`. Third-party components (JSBSim, FlightGear, PyQt5, pygame, OpenStreetMap) are not included and keep their own licenses.
