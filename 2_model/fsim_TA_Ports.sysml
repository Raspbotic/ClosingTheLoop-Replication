package FlightSimulator_TA_Ports {
    public import ScalarValues::*;

    package PortDefinitions {
        port def UdpPort {
            attribute address;
            attribute portNumber: Integer;
            attribute datagram;
        }
        
        port def HttpPort {
            attribute address;
            attribute portNumber: Integer;
            attribute datagram;
            attribute method; // "GET" or "POST"
        }
        
        port def HttpsPort :> HttpPort {
            attribute tlsEnabled : Boolean = true;
        }
        
        port def InternalSignalPort {
            attribute datagram;
        }
    }
}