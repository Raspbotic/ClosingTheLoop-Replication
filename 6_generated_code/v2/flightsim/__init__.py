"""FlightSim application package.

Recreated from the SysML v2 model, `fsim_Design_v5.xlsx` and the Decision Annex
under REPLICATION_CONTRACT_V2.

Provenance tags used throughout this package [a: contract Sec.2]:
  [a] backed by a supplied artifact, cited -- [a: L2SYS-REQ-nn.nn] or [a: Dn]
      or [a: <model file>]
  [b] derived from [a]
  [c] the agent's own choice, flagged inline with what would settle it

Process decomposition [a: fsim_TA_System.sysml], see docs/ARCHITECTURE.md:
  flightsim.hotas_standalone   HOTAS transmitter   (.venv_hotas, Python 3.11)
  flightsim.pcs.main_window    Pilot Control Station
  flightsim.master_node        Bus orchestrator
  flightsim.run_sem            Synthetic Environment Manager
"""

__all__ = ["common", "pcs"]
