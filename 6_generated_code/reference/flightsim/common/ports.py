"""
flightsim.common.ports
======================
Single source of truth for all network endpoints of the Flight Simulator.

TRACEABILITY
------------
Every constant below is extracted verbatim from the SysML v2 Technical
Architecture (TA). Category [a]: documented in the design specification.
The sysml source of each value is cited next to it.

No value in this file is invented; changing a port number here without
changing the TA model breaks design-code traceability.
"""

# ---------------------------------------------------------------------------
# Loopback host used by all sim-internal links
# [a] fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml, fsim_TA_SEM.sysml,
#     fsim_TA_PCS_ThreadPhysics.sysml: address = "127.0.0.1"
# ---------------------------------------------------------------------------
LOCALHOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# HOTAS -> PCS control link (UDP)
# [a] fsim_TA_Peripherals.sysml  HotasControl.e_hotasControlOut  portNumber = 5005
# [a] fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml  e_hotasControlIn portNumber = 5005
# ---------------------------------------------------------------------------
UDP_HOTAS_CONTROL = 5005

# ---------------------------------------------------------------------------
# PCS -> Orchestrator ownship state link (UDP)
# [a] fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml  e_ownshipStateOut portNumber = 5006
# [a] fsim_TA_Orch.sysml  pcsNode.e_ownshipStateIn               portNumber = 5006
# ---------------------------------------------------------------------------
UDP_PCS_OWNSHIP_STATE = 5006

# ---------------------------------------------------------------------------
# Orchestrator -> SEM ownship state link (UDP): SEM binds 5007
# [a] fsim_TA_Orch.sysml seNode.e_ownshipStateOut portNumber = 5007
# [a] HL architecture diagrams (JPEG + FlightSim_SEN_Architecture.txt):
#     "[UDP Port 5007] Ownship State" toward SEM
# NOTE [b]: fsim_TA_SEM.sysml currently says e_ownshipStateIn = 6007 --
#     model erratum confirmed by customer 2026-07-11, to be swapped.
# ---------------------------------------------------------------------------
UDP_SEM_OWNSHIP_STATE = 5007

# ---------------------------------------------------------------------------
# SEM -> Orchestrator radar detections link (UDP): orchestrator binds 6007
# [a] fsim_TA_Orch.sysml seNode.e_radarDetectionsIn portNumber = 6007
# [a] HL architecture diagrams: "[UDP Port 6007] Radar Detections" from SEM
# NOTE [b]: fsim_TA_SEM.sysml currently says e_radarDetectionsOut = 5007 --
#     same erratum, to be swapped.
# ---------------------------------------------------------------------------
UDP_SEM_RADAR_DETECTIONS = 6007

# ---------------------------------------------------------------------------
# Orchestrator -> PCS radar detections link (UDP)
# [a] fsim_TA_Orch.sysml  pcsNode.e_radarDetectionsOut portNumber = 6006
# [a] fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml e_radarDetectionsIn portNumber = 6006
# ---------------------------------------------------------------------------
UDP_PCS_RADAR_DETECTIONS = 6006

# ---------------------------------------------------------------------------
# PCS physics -> FlightGear native FDM link (UDP, binary FlightGear format,
# NOT JSON -- see messages.py note)
# [a] fsim_TA_PCS_ThreadPhysics.sysml e_nativeJSBSimFdmOut portNumber = 5550
# [a] fsim_TA_Peripherals.sysml FlightGearOTW.visualEngine.e_nativeFdmIn 5550
# ---------------------------------------------------------------------------
UDP_NATIVE_FDM = 5550

# ---------------------------------------------------------------------------
# FlightGear local HTTP server -> PCS ground elevation query (HTTP)
# [a] fsim_TA_Peripherals.sysml FlightGearOTW.localHttpServer
#     e_groundElevMeterOut portNumber = 8080
# [a] fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml e_groundElevMeterIn 8080
# ---------------------------------------------------------------------------
HTTP_GROUND_ELEV = 8080

# ---------------------------------------------------------------------------
# External OpenStreetMap tile server (HTTPS)
# [a] fsim_TA_PCS_ThreadMapWindow.sysml TileFetcher.e_HttpMapTiles
#     address = "tile.openstreetmap.org"
# [a] fsim_TA_Peripherals.sysml OpenStreetMapAPI.e_tile2DInterface
# [c] URL path template is standard OSM slippy-map convention (best guess,
#     not in the model): https://tile.openstreetmap.org/{z}/{x}/{y}.png
# ---------------------------------------------------------------------------
OSM_TILE_HOST = "tile.openstreetmap.org"
OSM_TILE_URL_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"