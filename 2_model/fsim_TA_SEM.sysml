package FlightSimulator_TA_SynthEnvManager {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;
    private import FlightSimulator_TA_Attributes::*;
    private import FlightSimulator_LogicalArchitecture::*;

    part def SyntheticEnvironmentMgr :> FlightSimulator_LogicalArchitecture::SyntheticEnvironment {
        // FIX 2026-10-15 final: LA restructure moved SyntheticEnvironment to
        // the package root; the nested FlightSimSystem:: path no longer exists.
        doc /* Radar/EW simulation (run_sem.py) */
 		attribute runtime : PythonRuntime {
			:>> version = "3.14";
			:>> rationale = "as per project main virtual environment (venv)";
		}                
        
        part threadMainGui {            
            action connectToSim;
            action manageEwScenario {
            	doc /* define beacon parameters, including per-beacon radar-equation parameters and ownship RCS */
				action manageEwBeacon;
				action controlOwnshipRcs;				            	
            }
            action updateSEUI;
            action calcBeaconOwnshipRange;
            action calcRadarEquation;
       			doc /* evaluate monostatic radar equation per beacon: emitter params
			        * (Pt, G, f, Pmin) + ownship RCS + slant range -> detection status
			        * and effective max detection range (published in the beacon msg) */
		}

        port e_ownshipStateIn : UdpPort { 
            :>> address = "127.0.0.1"; 
            :>> portNumber = 5007; 
        }
        port e_radarDetectionsOut : UdpPort { 
            :>> address = "127.0.0.1"; 
            :>> portNumber = 6007; 
        }
    }
}

