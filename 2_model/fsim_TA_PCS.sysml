package FlightSimulator_TA_PCS {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;
    private import FlightSimulator_TA_Attributes::*;
    private import FlightSimulator_LogicalArchitecture::*;
    private import FlightSimulator_TA_PCS_ThreadFlightSim::*;
    private import FlightSimulator_TA_PCS_ThreadPhysics::*;
    private import FlightSimulator_TA_PCS_ThreadMapWindow::*;

    part def PilotControlStation :> FlightSimulator_LogicalArchitecture::PilotControlStation {
        doc /* technical implementation */
		attribute runtime : PythonRuntime {
			:>> version = "3.14";
			:>> rationale = "as per project main virtual environment (venv)";
		}        
        // Boundary Ports of the PCS System (UPDATED with e_ prefix)
        port e_ownshipStateOut : UdpPort;
        port e_radarDetectionsIn : UdpPort;	            
        port e_hotasControlIn : UdpPort;	            
        port e_groundElevMeterIn : HttpPort;	            
        port e_nativeJSBSimFdmOut : UdpPort;
        port e_tile2DInterface : HttpsPort;    
        
        // Internal Thread Parts
        part threadFlightSim : ThreadFlightSim;
        part threadPhysics : ThreadPhysics;
        part threadMapWindow : ThreadMapWindow;

        // Port Delegation Bindings
        bind e_ownshipStateOut = threadFlightSim.e_ownshipStateOut;
        bind e_radarDetectionsIn = threadFlightSim.e_radarDetectionsIn;
        bind e_hotasControlIn = threadFlightSim.e_hotasControlIn;
        bind e_groundElevMeterIn = threadFlightSim.e_groundElevMeterIn;
        
        bind e_nativeJSBSimFdmOut = threadPhysics.e_nativeJSBSimFdmOut;
        
        bind e_tile2DInterface = threadMapWindow.e_HttpMapTiles;

        // Internal Thread Bindings
        bind threadFlightSim.i_HotasControlOut = threadPhysics.i_HotasControlIn;
        bind threadPhysics.i_OwnTelemetryOut = threadFlightSim.i_OwnTelemetryIn;
        
        // NEW: Map Bindings (Connecting UI to Map Window)
        bind threadFlightSim.i_OwnStateOut = threadMapWindow.i_OwnStateIn;
        bind threadFlightSim.i_BeaconDataOut = threadMapWindow.i_BeaconDataIn;
        bind threadFlightSim.i_TileCachePathOut = threadMapWindow.i_TileCachePathIn;
        bind threadMapWindow.i_2dTacticalMapOut = threadFlightSim.i_2dTileIn;
    }
}