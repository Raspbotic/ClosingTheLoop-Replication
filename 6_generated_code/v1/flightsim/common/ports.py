"""
flightsim/common/ports.py
=========================
Single source of truth for every network endpoint of the federation.

TRACEABILITY
------------
[a] Decision D4 "Port map (authoritative, as specified across the TA
    files)". Every constant below additionally names the SysML v2 port
    that fixes it, so the constant can be re-derived from the model
    alone. Verified consistent in BOTH directions in docs/ARCHITECTURE.md
    section 2 (orchestrator node port <-> peer subsystem boundary port).
[a] Interface requirements req_pcsRadarPort (6006) and req_semRadarPort
    (5007) of fsim_TA_IF_Requirements.sysml.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Host
# --------------------------------------------------------------------------

# [a] TA port attribute ":>> address = \"127.0.0.1\"" on every UdpPort that
#     carries one (fsim_TA_SEM.sysml, fsim_TA_PCS_ThreadPhysics.sysml,
#     fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml).
LOCALHOST = "127.0.0.1"

# --------------------------------------------------------------------------
# UDP ports of the tactical federation
# --------------------------------------------------------------------------

# HOTAS -> PCS.
# [a: D4 "HOTAS->PCS 5005"]
# [a] HotasControl.e_hotasControlOut :>> portNumber = 5005
#     (fsim_TA_Peripherals.sysml)
# [a] BackgroundIOManager.e_hotasControlIn :>> portNumber = 5005
#     (fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml)
UDP_HOTAS_CONTROL = 5005

# PCS -> Bus Orchestrator (ownship state).
# [a: D4 "PCS->orch 5006"]
# [a] BackgroundIOManager.e_ownshipStateOut :>> portNumber = 5006
#     (fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml)
# [a] BusOrchestrator.pcsNode.e_ownshipStateIn :>> portNumber = 5006
#     (fsim_TA_Orch.sysml)
UDP_PCS_TO_ORCH = 5006

# Bus Orchestrator -> SEM (ownship state).
# [a: D4 "orch->SEM 5007"]
# [a] BusOrchestrator.seNode.e_ownshipStateOut :>> portNumber = 5007
#     (fsim_TA_Orch.sysml)
# [a] SyntheticEnvironmentMgr.e_ownshipStateIn :>> portNumber = 5007
#     (fsim_TA_SEM.sysml)
# [a] req_semRadarPort.expectedPort = 5007 (fsim_TA_IF_Requirements.sysml)
UDP_ORCH_TO_SEM = 5007

# SEM -> Bus Orchestrator (radar detections).
# [a: D4 "SEM->orch 6007"]
# [a] SyntheticEnvironmentMgr.e_radarDetectionsOut :>> portNumber = 6007
#     (fsim_TA_SEM.sysml)
# [a] BusOrchestrator.seNode.e_radarDetectionsIn :>> portNumber = 6007
#     (fsim_TA_Orch.sysml)
UDP_SEM_TO_ORCH = 6007

# Bus Orchestrator -> PCS (radar detections).
# [a: D4 "orch->PCS 6006"]
# [a] BusOrchestrator.pcsNode.e_radarDetectionsOut :>> portNumber = 6006
#     (fsim_TA_Orch.sysml)
# [a] BackgroundIOManager.e_radarDetectionsIn :>> portNumber = 6006
#     (fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml)
# [a] req_pcsRadarPort.expectedPort = 6006 (fsim_TA_IF_Requirements.sysml)
UDP_ORCH_TO_PCS = 6006

# PCS physics -> FlightGear native FDM.
# [a: D4 "PCS->FlightGear native-fdm 5550"]
# [a] ThreadPhysics.e_nativeJSBSimFdmOut :>> portNumber = 5550
#     (fsim_TA_PCS_ThreadPhysics.sysml)
# [a] FlightGearOTW.visualEngine.e_nativeFdmIn :>> portNumber = 5550
#     (fsim_TA_Peripherals.sysml)
UDP_FG_NATIVE_FDM = 5550

# --------------------------------------------------------------------------
# HTTP / HTTPS endpoints
# --------------------------------------------------------------------------

# FlightGear built-in httpd.
# [a: D4 "FG httpd 8080, ground elevation at /json/position/ground-elev-m"]
# [a] FlightGearOTW.localHttpServer.e_groundElevMeterOut :>> portNumber = 8080
#     (fsim_TA_Peripherals.sysml)
# [a] BackgroundIOManager.e_groundElevMeterIn :>> portNumber = 8080
#     (fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml)
HTTP_FG_HTTPD = 8080
FG_GROUND_ELEV_PATH = "/json/position/ground-elev-m"

# OpenStreetMap tile service.
# [a: D4 "OSM tiles https://tile.openstreetmap.org"]
# [a] OpenStreetMapAPI.e_tile2DInterface :>> address = "tile.openstreetmap.org"
#     (fsim_TA_Peripherals.sysml); TileFetcher.e_HttpMapTiles carries the same
#     address (fsim_TA_PCS_ThreadMapWindow.sysml)
OSM_TILE_HOST = "tile.openstreetmap.org"
OSM_TILE_BASE_URL = "https://tile.openstreetmap.org"

# [b] Derived: the standard slippy-map path shape of the service named above.
OSM_TILE_URL_TEMPLATE = OSM_TILE_BASE_URL + "/{z}/{x}/{y}.png"

# [c] Best guess: OSM's tile usage policy requires an identifying UA string.
#     Not a specification item; flagged inline.
OSM_USER_AGENT = "fsim-demonstrator/1.0 (SysML v2 recreation build)"

# --------------------------------------------------------------------------
# Rates
# --------------------------------------------------------------------------

# [a] L2SYS-REQ-01.02b: "... at a 120Hz frequency".
PHYSICS_RATE_HZ = 120.0

# [a: D4] "... decimated to ~60 Hz" for the FGNetFDM stream.
FG_FDM_RATE_HZ = 60.0

# [a: D9] "Poll FG ground elevation at 2 Hz".
TERRAIN_POLL_HZ = 2.0

# [c] HOTAS transmit rate: L2SYS-REQ-01.01b says "continuously" without a
#     figure; 60 Hz halves the 120 Hz physics rate. Flagged as a developer
#     choice, adjustable via --rate.
HOTAS_RATE_HZ = 60.0

# [b] Ownship state publication rate towards the SEM. The model fixes no
#     rate on link L2; 10 Hz is derived as sufficient for a radar-range
#     evaluation loop while keeping the bus quiet.
OWNSHIP_PUBLISH_HZ = 10.0

__all__ = [
    "LOCALHOST",
    "UDP_HOTAS_CONTROL",
    "UDP_PCS_TO_ORCH",
    "UDP_ORCH_TO_SEM",
    "UDP_SEM_TO_ORCH",
    "UDP_ORCH_TO_PCS",
    "UDP_FG_NATIVE_FDM",
    "HTTP_FG_HTTPD",
    "FG_GROUND_ELEV_PATH",
    "OSM_TILE_HOST",
    "OSM_TILE_BASE_URL",
    "OSM_TILE_URL_TEMPLATE",
    "OSM_USER_AGENT",
    "PHYSICS_RATE_HZ",
    "FG_FDM_RATE_HZ",
    "TERRAIN_POLL_HZ",
    "HOTAS_RATE_HZ",
    "OWNSHIP_PUBLISH_HZ",
]
