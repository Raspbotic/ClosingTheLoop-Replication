"""Port constants of the federation.

Every constant is transcribed from the TA element that states it. Both ends of each
link carry the same number (cross-checked in docs/R1_ARCHITECTURE.md).
"""

LOOPBACK = "127.0.0.1"  # [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml#BackgroundIOManager::e_ownshipStateOut address]

# L1 HOTAS -> PCS
HOTAS_CONTROL_PORT = 5005  # [a: fsim_TA_Peripherals.sysml#HotasControl::e_hotasControlOut] [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml#BackgroundIOManager::e_hotasControlIn]
# L2 PCS -> orchestrator pcsNode
PCS_OWNSHIP_OUT_PORT = 5006  # [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml#BackgroundIOManager::e_ownshipStateOut] [a: fsim_TA_Orch.sysml#pcsNode::e_ownshipStateIn]
# L3 orchestrator seNode -> SEM
SEM_OWNSHIP_IN_PORT = 5007  # [a: fsim_TA_Orch.sysml#seNode::e_ownshipStateOut] [a: fsim_TA_SEM.sysml#SyntheticEnvironmentMgr::e_ownshipStateIn]
# L4 SEM -> orchestrator seNode
SEM_RADAR_OUT_PORT = 6007  # [a: fsim_TA_SEM.sysml#SyntheticEnvironmentMgr::e_radarDetectionsOut] [a: fsim_TA_Orch.sysml#seNode::e_radarDetectionsIn]
# L5 orchestrator pcsNode -> PCS
PCS_RADAR_IN_PORT = 6006  # [a: fsim_TA_Orch.sysml#pcsNode::e_radarDetectionsOut] [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml#BackgroundIOManager::e_radarDetectionsIn]
# L6 PCS physics -> FlightGear visual engine
NATIVE_FDM_PORT = 5550  # [a: fsim_TA_PCS_ThreadPhysics.sysml#ThreadPhysics::e_nativeJSBSimFdmOut] [a: fsim_TA_Peripherals.sysml#FlightGearOTW::visualEngine::e_nativeFdmIn]
# L7 FlightGear httpd -> PCS
FG_HTTPD_PORT = 8080  # [a: fsim_TA_Peripherals.sysml#FlightGearOTW::localHttpServer::e_groundElevMeterOut] [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml#BackgroundIOManager::e_groundElevMeterIn]
# L8 tile service
OSM_TILE_HOST = "tile.openstreetmap.org"  # [a: fsim_TA_PCS_ThreadMapWindow.sysml#TileFetcher::e_HttpMapTiles] [a: fsim_TA_Peripherals.sysml#OpenStreetMapAPI::e_tile2DInterface]

FG_GROUND_ELEV_PATH = "/json/position/ground-elev-m"  # [c] FlightGear property path; model names only the port. Settled by the FlightGear httpd documentation.

# Rates the model does not state
HOTAS_RATE_HZ = 60.0        # [b: legacy hotas_standalone.py time.sleep(1/60)]
PHYSICS_RATE_HZ = 120.0     # [a: L2SYS-REQ-01.02b] [a: fsim_TA_PCS_ThreadPhysics.sysml#cyclicExecutionLoop "120Hz"]
NATIVE_FDM_RATE_HZ = 60.0   # [c] model silent (F5); settled by a model rate attribute
OWNSHIP_PUBLISH_HZ = 20.0   # [c] model silent (F5)
TERRAIN_POLL_HZ = 2.0       # [a: Behaviour B7] model silent
