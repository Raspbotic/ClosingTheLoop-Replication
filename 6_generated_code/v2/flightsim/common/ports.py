"""The port map. One constant per link, each cited to its TA source and to D4.

Every number below appears in **three** places in the supplied package -- the
source port declaration, the sink port declaration, and an `expectedPort` in
`fsim_TA_IF_Requirements.sysml` -- and all three agree with D4. The R-1
cross-check is in docs/ARCHITECTURE.md Sec.4; `tools/closure_audit.py` check 4
re-derives it from `model/*.sysml` at audit time rather than trusting this file.

Nothing here binds a socket. Which side binds and which side sends is a property
of the process, not of the constant. [b]
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------
# 127.0.0.1 is stated explicitly on the SEM's two ports
# [a: fsim_TA_SEM.sysml], on all four BackgroundIOManager ports
# [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml] and on the physics FDM port
# [a: fsim_TA_PCS_ThreadPhysics.sysml]. It is NOT stated on the HOTAS port, on
# either FlightGear port, or on any BusOrchestrator port -- see
# docs/ARCHITECTURE.md A-I3, reported not resolved. Used throughout. [b]
LOOPBACK = "127.0.0.1"

#: Bind address for a receiver. All-interfaces so a datagram from any local
#: source is accepted; the federation is loopback-only in the model. [c] --
#: what would settle it: a stated deployment requirement for a multi-host
#: federation, which neither the model nor the workbook contains.
BIND_ADDRESS = "0.0.0.0"

# ---------------------------------------------------------------------------
# UDP links [a: D4]
# ---------------------------------------------------------------------------

#: HOTAS -> PCS.
#: [a: D4] HOTAS->PCS 5005
#: [a: fsim_TA_Peripherals.sysml] HotasControl.e_hotasControlOut.portNumber = 5005
#: [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml] e_hotasControlIn.portNumber = 5005
#: [a: fsim_TA_IF_Requirements.sysml] req_hotasControlOutPort / req_pcsHotasControlInPort
HOTAS_TO_PCS = 5005

#: PCS -> orchestrator.
#: [a: D4] PCS->orchestrator 5006
#: [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml] e_ownshipStateOut.portNumber = 5006
#: [a: fsim_TA_Orch.sysml] pcsNode.e_ownshipStateIn.portNumber = 5006
#: [a: fsim_TA_IF_Requirements.sysml] req_pcsOwnshipStateOutPort / req_orchOwnshipStateInPort
PCS_TO_ORCH = 5006

#: orchestrator -> SEM.
#: [a: D4] orchestrator->SEM 5007
#: [a: fsim_TA_Orch.sysml] seNode.e_ownshipStateOut.portNumber = 5007
#: [a: fsim_TA_SEM.sysml] e_ownshipStateIn.portNumber = 5007
#: [a: fsim_TA_IF_Requirements.sysml] req_orchOwnshipStateOutPort / req_semOwnshipStateInPort
ORCH_TO_SEM = 5007

#: SEM -> orchestrator.
#: [a: D4] SEM->orchestrator 6007
#: [a: fsim_TA_SEM.sysml] e_radarDetectionsOut.portNumber = 6007
#: [a: fsim_TA_Orch.sysml] seNode.e_radarDetectionsIn.portNumber = 6007
#: [a: fsim_TA_IF_Requirements.sysml] req_semRadarDetectionsOutPort / req_orchRadarDetectionsInPort
SEM_TO_ORCH = 6007

#: orchestrator -> PCS.
#: [a: D4] orchestrator->PCS 6006
#: [a: fsim_TA_Orch.sysml] pcsNode.e_radarDetectionsOut.portNumber = 6006
#: [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml] e_radarDetectionsIn.portNumber = 6006
#: [a: fsim_TA_IF_Requirements.sysml] req_orchRadarDetectionsOutPort / req_pcsRadarDetectionsInPort
ORCH_TO_PCS = 6006

#: PCS physics -> FlightGear native-fdm.
#: [a: D4] PCS->FlightGear native-fdm 5550
#: [a: fsim_TA_PCS_ThreadPhysics.sysml] e_nativeJSBSimFdmOut.portNumber = 5550
#: [a: fsim_TA_Peripherals.sysml] FlightGearOTW.visualEngine.e_nativeFdmIn.portNumber = 5550
#: [a: fsim_TA_IF_Requirements.sysml] req_pcsNativeFdmOutPort / req_fgNativeFdmInPort
PCS_TO_FLIGHTGEAR_FDM = 5550

# ---------------------------------------------------------------------------
# HTTP / HTTPS endpoints [a: D4]
# ---------------------------------------------------------------------------

#: FlightGear's built-in httpd. This is a port the PCS **connects to**, not one
#: it binds; the model gives it as a source port on FlightGear and a sink port
#: on the PCS, both numbered 8080, which is the server's listening port on both
#: ends. See docs/ARCHITECTURE.md A-I4, reported not resolved. [b]
#: [a: D4] FlightGear httpd 8080
#: [a: fsim_TA_Peripherals.sysml] localHttpServer.e_groundElevMeterOut.portNumber = 8080
#: [a: fsim_TA_PCS_ThreadFlightSim_BkgIOM.sysml] e_groundElevMeterIn.portNumber = 8080
#: [a: fsim_TA_IF_Requirements.sysml] req_fgHttpdOutPort / req_pcsGroundElevInPort
FLIGHTGEAR_HTTPD = 8080

#: The ground-elevation resource on that httpd. [a: D4]
FLIGHTGEAR_GROUND_ELEV_PATH = "/json/position/ground-elev-m"

#: OpenStreetMap tile service. Neither end of the link carries a portNumber in
#: the model; 443 is implied by `HttpsPort.tlsEnabled = true`
#: [a: fsim_TA_Ports.sysml]. D4 gives the origin, so the origin is the constant.
#: [a: D4] OSM tiles at https://tile.openstreetmap.org
#: [a: fsim_TA_Peripherals.sysml] OpenStreetMapAPI.e_tile2DInterface.address
#: [a: fsim_TA_PCS_ThreadMapWindow.sysml] TileFetcher.e_HttpMapTiles.address
#: [a: fsim_TA_IF_Requirements.sysml] req_osmTileHost.expectedAddress
OSM_TILE_HOST = "tile.openstreetmap.org"
OSM_TILE_ORIGIN = "https://tile.openstreetmap.org"

#: Slippy-tile path template. [b: D24, D4]
OSM_TILE_PATH = "/{z}/{x}/{y}.png"

# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

#: Every UDP port this project binds or sends to, for the "close running
#: instances" caveat of D15 and for closure-audit check 4. [b]
ALL_UDP_PORTS = (
    HOTAS_TO_PCS,
    PCS_TO_ORCH,
    ORCH_TO_SEM,
    SEM_TO_ORCH,
    ORCH_TO_PCS,
    PCS_TO_FLIGHTGEAR_FDM,
)

#: Maximum UDP payload this project reads in one recv. A RadarBeacon or an
#: OwnshipState encodes to a few hundred bytes; 65535 is the datagram ceiling,
#: so no message can be truncated by this buffer. [c] -- what would settle a
#: smaller value: a stated MTU constraint, which the model does not give.
MAX_DATAGRAM = 65535


def tile_url(z: int, x: int, y: int) -> str:
    """Absolute URL of one OSM tile. [a: D4, D24]"""
    return OSM_TILE_ORIGIN + OSM_TILE_PATH.format(z=z, x=x, y=y)


def ground_elev_url(host: str = LOOPBACK, port: int = FLIGHTGEAR_HTTPD) -> str:
    """Absolute URL of FlightGear's ground-elevation property. [a: D4]"""
    return f"http://{host}:{port}{FLIGHTGEAR_GROUND_ELEV_PATH}"
