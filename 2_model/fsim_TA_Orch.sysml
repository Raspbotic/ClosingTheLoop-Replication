package FlightSimulator_TA_Orch {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;
    private import FlightSimulator_TA_Attributes::*;
    private import FlightSimulator_LogicalArchitecture::*;

    part def BusOrchestrator :> FlightSimulator_LogicalArchitecture::BusManager {
        doc /* Network middleware (master_node.py) */
		attribute runtime : PythonRuntime {
			:>> version = "3.14";
			:>> rationale = "as per project main virtual environment (venv)";
		}                
        // Boundary Po rts
        port e_ownshipStateOut : UdpPort;
        port e_radarDetectionsIn : UdpPort;
        port e_radarDetectionsOut : UdpPort;
        port e_ownshipStateIn : UdpPort;

        part seNode {
            action seNodeAction1;
            port e_ownshipStateOut : UdpPort { :>> portNumber = 5007; }
            port e_radarDetectionsIn : UdpPort { :>> portNumber = 6007; }
            port i_Pcs2SeIn : InternalSignalPort;
            port i_Se2PcsOut : InternalSignalPort;
        } 
        
        part pcsNode {
            action pcsNodeAction1;
            port e_radarDetectionsOut : UdpPort { :>> portNumber = 6006; }
            port e_ownshipStateIn : UdpPort { :>> portNumber = 5006; }
            port i_Pcs2SeOut : InternalSignalPort;
            port i_Se2PcsIn : InternalSignalPort;
         } 
        
        part busRelay;

        // Route outer boundary to the specific internal nodes
        bind e_ownshipStateOut = seNode.e_ownshipStateOut;
        bind e_radarDetectionsIn = seNode.e_radarDetectionsIn;
        
        bind e_radarDetectionsOut = pcsNode.e_radarDetectionsOut;
        bind e_ownshipStateIn = pcsNode.e_ownshipStateIn;

        // Connecting the two peer nodes inside the orchestrator
        connect seNode.i_Se2PcsOut to pcsNode.i_Se2PcsIn;
        connect pcsNode.i_Pcs2SeOut to seNode.i_Pcs2SeIn;
    }
}