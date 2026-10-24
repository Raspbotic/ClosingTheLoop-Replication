# ClosingTheLoop-Replication

Replication package for the article

> G. Marsiglia, "Regenerating a Cyber-Physical System with a Large Language Model under a SysML v2-Based Execution Contract," submitted to *Systems Engineering* (Wiley/INCOSE), 2026.

Author: Gianluca Marsiglia, independent researcher, Ingolstadt, Germany. ORCID https://orcid.org/0009-0007-0260-0041.
Archived version: Zenodo DOI to be added at acceptance. Please cite the article and this package as given in `CITATION.cff`.

The package contains the items listed in Section 6.5 of the article and in its Data Availability Statement.

## Contents

| Folder | Contents |
|---|---|
| `1_contract/` | The four execution contracts, `REPLICATION_CONTRACT_V1.md` to `_V4.md`, as handed to the agent (version 4 is the form described in Section 4 of the article), and `DECISIONS_TO_BEHAVIORS.md`, the mapping of the 24 decisions of version 2 to the 15 behaviours of version 4 (Section 5.2). |
| `2_model/` | The SysML v2 model as handed to version 4: the 18 required files plus the optional `fsim_Functions.sysml`. The verification matrix `fsim_RVM.sysml` is included with the verification evidence (test names) removed (Section 5.1). |
| `3_requirements/` | The requirements workbook `fsim_Design_v5.xlsx`, from which the requirement layer, the allocation matrices and the verification matrix of the model are generated (Section 5.1). |
| `4_references/` | The reference screenshots `Desired_UIUX_capture.JPG` and `Desired_FGear_capture.JPG`, and the transmitter script `hotas_standalone.py` of Section 5.1. |
| `5_virtual_environments/` | The pinned Python requirements files of the two virtual environments (`requirements_main.txt`, Python 3.14; `requirements_hotas.txt`, Python 3.11), as delivered with version 4; they also serve the version-1 and version-2 builds. |
| `6_generated_code/` | One folder per delivered build. `v1/`, `v2/` and `v4/` hold the application package, the test suite, the closure-audit output, the provenance census and the session log, as consolidated (Figure 1 of the article). `reference/` holds, for the reference build of Section 5.2, the prompt protocol of the interactive session (`replication_prompts.md`, 57 entries condensed from 59 user turns), the test-run record (`TestRun_Results.txt`, 56 tests) and the hand-made provenance census; the code of the reference build is not part of the package. Version 3 produced no code (Section 5.3) and has no folder. |

## Checklist against Section 6.5

| Item | Where |
|---|---|
| Four contract texts | `1_contract/` |
| Model with verification evidence removed | `2_model/` |
| Requirements workbook | `3_requirements/` |
| Reference screenshots | `4_references/` |
| Transmitter script | `4_references/hotas_standalone.py` |
| Mapping from the 24 decisions to the 15 behaviours | `1_contract/DECISIONS_TO_BEHAVIORS.md` |
| Requirements files of each virtual environment | `5_virtual_environments/` |
| Delivered applications, suites, audit outputs, censuses, session logs | `6_generated_code/<build>/` |

## Reading the figures of Table 2

The test counts are pytest items; the closure audits also report test functions, a smaller number. The provenance census of versions 1 and 2 is the file `PROVENANCE.md` of the build; for version 4 it is the census line of `CLOSURE_AUDIT.md`. The census counts tag occurrences, self-reported by the agent; each audit states the files it counted (application code for versions 2 and 4, application and test code for version 1). The article normalizes the counts by the lines of application and test code in the same way for every version. The reference build, generated before the tag instruction existed, was tagged and counted by hand afterwards.

Session logs were exported from the agent's interface and redacted of local paths, user names, machine names and keys; redactions are marked `[redacted]`.

## Running version 4

Windows, Python 3.14 and 3.11 installed. Create the two virtual environments from `5_virtual_environments/`, then in `6_generated_code/v4/`: `run_tests.bat` runs the suite headless; `launch_flightsim.bat` starts the orchestrator, the synthetic environment manager, the pilot control station and the HOTAS transmitter in demo mode. FlightGear and a physical HOTAS are optional. Map tiles need network access to the OpenStreetMap tile service.

## Licenses

Software (the generated applications and tests, scripts, manifests, the transmitter script): MIT, see `LICENSE`. Documentation and specification artifacts (contracts, mapping, model, workbook, screenshots, audit outputs, censuses, session logs): CC BY 4.0, see `LICENSE-DOCS`. Third-party components (JSBSim, FlightGear, PyQt5, pygame, OpenStreetMap) are not included and keep their own licenses.
