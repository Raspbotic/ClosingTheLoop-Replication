package FlightSimulator_TA_PCS_ThreadMapWindow {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;
    private import FlightSimulator_LogicalArchitecture::*;

    // --- Submodule 1: Main GUI Map Renderer ---
    part def MapGuiWidget {
        doc /* Runs on the Main PCS Thread: handles coordinate math and Qt rendering */
        
        // Actions
        action processOwnshipState;
        action processBeaconContacts;
        action calculateTileCoordinates;
        action render2dTacticalMap;
        action push2dTacticalMap;

        // Submodule Boundary Ports (From Parent)
        port i_OwnStateIn : InternalSignalPort;
        port i_BeaconDataIn : InternalSignalPort;
        port i_2dTacticalMapOut : InternalSignalPort;
        
        // Internal Worker Ports (To TileFetcher)
        port i_TileQueueOut : InternalSignalPort; 
        port i_TileReadyIn : InternalSignalPort; 
    }

    // --- Submodule 2: Background Network Worker ---
    part def TileFetcher {
        doc /* Runs on a background QThread: handles asynchronous HTTPS requests */
        
        // Actions
        action requestExternalTiles;
        action cacheMapTiles;

        // Submodule Boundary Ports
        port i_TileQueueIn : InternalSignalPort;
        port i_TileReadyOut : InternalSignalPort;
        port i_TileCachePathIn : InternalSignalPort;
        
        // External Network Port
        port e_HttpMapTiles : HttpsPort { 
            :>> address = "tile.openstreetmap.org"; 
        }
    }

    // --- Main Assembly (Strict Delegation) ---
    part def ThreadMapWindow :> FlightSimulator_LogicalArchitecture::Tactical2DMap {
        doc /* Map Rendering Layer with Asynchronous Tile Fetching (map_window.py) */
        
        // Instantiate the submodules
        part mapGuiWorker : MapGuiWidget;
        part tileFetchWorker : TileFetcher;

        // --- PARENT BOUNDARY PORTS ---
        port i_OwnStateIn : InternalSignalPort;
        port i_BeaconDataIn : InternalSignalPort;
        port i_2dTacticalMapOut : InternalSignalPort;
        port i_TileCachePathIn : InternalSignalPort;
        port e_HttpMapTiles : HttpsPort;
        
        // --- STRICT DELEGATION BINDINGS ---
        // 1. Route Parent Boundary -> Submodules
        bind i_OwnStateIn = mapGuiWorker.i_OwnStateIn;
        bind i_BeaconDataIn = mapGuiWorker.i_BeaconDataIn;
        bind i_2dTacticalMapOut = mapGuiWorker.i_2dTacticalMapOut;
        bind e_HttpMapTiles = tileFetchWorker.e_HttpMapTiles;
        bind i_TileCachePathIn = tileFetchWorker.i_TileCachePathIn;

        // 2. Submodule to Submodule Bindings
        // Connect the GUI Queue to the Fetcher, and the Fetcher Signal back to the GUI
        bind mapGuiWorker.i_TileQueueOut = tileFetchWorker.i_TileQueueIn;
        bind tileFetchWorker.i_TileReadyOut = mapGuiWorker.i_TileReadyIn;
    }
}