package FlightSimulator_TA_PCS_ThreadFlightSim {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;

    // Import the separated submodules
    private import FlightSimulator_TA_PCS_ThreadFlightSim_UIManager::*;
    private import FlightSimulator_TA_PCS_ThreadFlightSim_BckgIOManager::*;

    part def ThreadFlightSim {
        doc /* UI and I/O Loop Assembly (main_window.py) */
        
        // Instantiate the submodules
        part uiManager : UserInterfaceManager;
        part ioManager : BackgroundIOManager;

        // --- PARENT BOUNDARY PORTS ---
        // Internal Inter-Thread Signaling Ports 
        port i_OwnTelemetryIn : InternalSignalPort;
        port i_HotasControlOut : InternalSignalPort;
        port i_2dTileIn : InternalSignalPort;
        port i_TileCachePathOut : InternalSignalPort;
        
        // NEW: Output ports to feed the Map Window
        port i_OwnStateOut : InternalSignalPort;
        port i_BeaconDataOut : InternalSignalPort;

        // External Network Ports 
        port e_ownshipStateOut : UdpPort;
        port e_radarDetectionsIn : UdpPort;	            
        port e_hotasControlIn : UdpPort;	            
        port e_groundElevMeterIn : HttpPort;

        // --- STRICT DELEGATION BINDINGS ---
        // Route Parent Internal Boundary -> UI Manager
        bind i_OwnTelemetryIn = uiManager.i_OwnTelemetryIn;
        bind i_HotasControlOut = uiManager.i_HotasControlOut;
        bind i_2dTileIn = uiManager.i_2dTileIn;
        bind i_TileCachePathOut = uiManager.i_TileCachePathOut;
        bind i_OwnStateOut = uiManager.i_OwnStateOut;
        bind i_BeaconDataOut = uiManager.i_BeaconDataOut;

        // Route Parent External Boundary -> IO Manager
        bind e_ownshipStateOut = ioManager.e_ownshipStateOut;
        bind e_radarDetectionsIn = ioManager.e_radarDetectionsIn;
        bind e_hotasControlIn = ioManager.e_hotasControlIn;
        bind e_groundElevMeterIn = ioManager.e_groundElevMeterIn;
    }
}