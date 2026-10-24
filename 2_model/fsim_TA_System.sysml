package FlightSimulator_TA_System {
    public import FlightSimulator_TA_Ports::PortDefinitions::*;
    public import FlightSimulator_TA_PCS::*;
    public import FlightSimulator_TA_SynthEnvManager::*;
    public import FlightSimulator_TA_Orch::*;
    public import FlightSimulator_TA_Peripherals::*;
    private import FlightSimulator_LogicalArchitecture::*;

    part def FlightSimSystem :> FlightSimulator_LogicalArchitecture::FlightSimSystem {
        
        part sem : FlightSimulator_TA_SynthEnvManager::SyntheticEnvironmentMgr redefines syntheticEnvironment;
        part pcs : FlightSimulator_TA_PCS::PilotControlStation;
        part simbus : FlightSimulator_TA_Orch::BusOrchestrator;
        
        ref part fgfs : FlightSimulator_TA_Peripherals::FlightGearOTW redefines visualSystem;
        ref part hotas : FlightSimulator_TA_Peripherals::HotasControl;
        ref part osm : FlightSimulator_TA_Peripherals::OpenStreetMapAPI;

        // ---------------------------------------------------
        // External Interface Connections (Boundary to Boundary)
        // ---------------------------------------------------
        
        // SEM <-> SEN MW (ORCHESTRATOR BOUNDARY)
        connect sem.e_radarDetectionsOut to simbus.e_radarDetectionsIn;
        connect simbus.e_ownshipStateOut to sem.e_ownshipStateIn;

        // SEN MW (ORCHESTRATOR BOUNDARY) <-> PCS
        connect pcs.e_ownshipStateOut to simbus.e_ownshipStateIn;
        connect simbus.e_radarDetectionsOut to pcs.e_radarDetectionsIn;

        // HOTAS -> PCS
        connect hotas.e_hotasControlOut to pcs.e_hotasControlIn;

        // PCS <-> FGFS (FlightGear)
        connect fgfs.localHttpServer.e_groundElevMeterOut to pcs.e_groundElevMeterIn;
        connect pcs.e_nativeJSBSimFdmOut to fgfs.visualEngine.e_nativeFdmIn;

        // PCS <-> OpenStreetMap 
        connect pcs.e_tile2DInterface to osm.e_tile2DInterface;
    }
}