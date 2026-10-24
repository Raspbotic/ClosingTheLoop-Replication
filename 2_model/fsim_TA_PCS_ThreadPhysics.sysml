package FlightSimulator_TA_PCS_ThreadPhysics {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;
    private import FlightSimulator_LogicalArchitecture::*;

    part def ThreadPhysics :>
        FlightSimulator_LogicalArchitecture::VirtualOwnship {
        // FIX 2026-10-15 final: LA restructure moved VirtualOwnship to the
        // package root; the nested FlightSimSystem:: path no longer exists.
        doc /* FDM Engine Interface (physics.py) */
        
        // Group 1: Initialization (Aperiodic)
        action initializationPhase {
            action initFDM;
            action setInitialConditions;
            action updateEnvironment;
        }
        
        // Group 2: The 120Hz Loop (Periodic)
        action cyclicExecutionLoop {
            action acquireControlInputs;
            action mapInputsToFDM;
            action executePhysicsStep;
            action extractTelemetry;
            action trasmitTelemetrySignal;
            action trasmitVisualTelemetry;
        }

        // Ports remain at the thread level, as it acts as a single boundary
        port i_OwnTelemetryOut : InternalSignalPort;
        port i_HotasControlIn : InternalSignalPort;
        
        port e_nativeJSBSimFdmOut : UdpPort {
            :>> address = "127.0.0.1";
            :>> portNumber = 5550;
        }
    }
}