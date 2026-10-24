package FlightSimulator_LogicalArchitecture {
    public import ScalarValues::*;

    // definition blueprints at the package root
    part def SyntheticEnvironment {}
    part def VisualSystem {}
    part def Hotas {}
    part def VirtualOwnship {}
    part def Tactical2DMap {}
    part def BusManager {}
    part def PilotControlStation {
        part tactical2DMap : Tactical2DMap;
        ref part hotas : Hotas;
    }
    part def FlightSimSystem {
        part virtualOwnship : VirtualOwnship;
        part pilotControlStation : PilotControlStation;
        part busManager : BusManager;
        part syntheticEnvironment : SyntheticEnvironment;
        ref part visualSystem : VisualSystem;
    }
    part flightSimSystem : FlightSimSystem;
}