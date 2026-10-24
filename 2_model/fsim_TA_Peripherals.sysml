package FlightSimulator_TA_Peripherals {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;
    private import FlightSimulator_LogicalArchitecture::*;
	private import FlightSimulator_TA_Attributes::*;

    part def HotasControl :> FlightSimulator_LogicalArchitecture::Hotas {
        doc /* hotas_standalone.py */
		attribute runtime : PythonRuntime {
			:>> version = "3.11";
			:>> rationale = "pygame: no cp314 wheels available";
		}        
		action measureStickDeflection;
        action measureThrottlePosition;
        action measureYawTwist;
        action readSwitches;
        action formatControlPayload;
        action transmitControlData;
        port e_hotasControlOut : UdpPort { :>> portNumber = 5005; }
    }

    part def FlightGearOTW :> FlightSimulator_LogicalArchitecture::VisualSystem {
        doc /* FlightGear Out-Of-The-Window Visuals (fgfs.exe) */
        part visualEngine {	    	
            action processOwnshipState;
            action calcOwnHeightAboveGround;       	
            action renderOtw;       	
            port e_nativeFdmIn : UdpPort { :>> portNumber = 5550; }
        }
        part localHttpServer {
            action txOwnHeightAboveGround;
            port e_groundElevMeterOut : HttpPort { :>> portNumber = 8080; }        	
        }
    }

    part def OpenStreetMapAPI {
        doc /* External map tile server */      
        action processTileRequest;
        
        // FIXED: Consolidated into a single bidirectional HTTPS Port
        port e_tile2DInterface : HttpsPort { 
            :>> address = "tile.openstreetmap.org"; 
        } 
    }
}