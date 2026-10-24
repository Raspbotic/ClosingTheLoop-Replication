package FlightSimulator_TA_PCS_ThreadFlightSim_BckgIOManager {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;

    part def BackgroundIOManager {
        doc /* Handles background QTimers, network bridging, and telemetry capture */
        
        // I/O Actions
        action bridgeUdpData;
        action telemetryCaptureManager;
        action terrainElevationManager;

        // Submodule Boundary Ports (External Network)
        port e_ownshipStateOut : UdpPort { 
            :>> address = "127.0.0.1"; 
            :>> portNumber = 5006; 
        }
        port e_radarDetectionsIn : UdpPort { 
            :>> address = "127.0.0.1"; 
            :>> portNumber = 6006; 
        }	            
        port e_hotasControlIn : UdpPort { 
            :>> address = "127.0.0.1"; 
            :>> portNumber = 5005; 
        }	            
        port e_groundElevMeterIn : HttpPort { 
            :>> address = "127.0.0.1"; 
            :>> portNumber = 8080; 
        }
    }
}