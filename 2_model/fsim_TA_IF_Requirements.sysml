package FlightSimulator_IF_Requirements {
    public import ScalarValues::*;
    private import FlightSimulator_TA_Ports::PortDefinitions::*;
    private import FlightSimulator_TA_System::*;

    doc /* Interface (port assignment) requirements for the D4 link map.
     *
     * CORRECTION (round 6). The previous version bound every subject to a
     * port reached through a part DEFINITION:
     *
     *     subject boundaryPort = HotasControl::e_hotasControlOut;
     *     subject boundaryPort = BusOrchestrator::pcsNode::e_ownshipStateIn;
     *
     * `HotasControl` is a `part def`. `HotasControl::e_hotasControlOut`
     * navigates the definition's namespace, so it names a FEATURE, not a
     * value - there is no port there to compare a number against. This is
     * the same defect that was corrected in fsim_R2F.sysml ("satisfy needs
     * a part usage, not a part def"); it was never applied here.
     *
     * Every subject is now bound through the one FlightSimSystem USAGE
     * declared in `ifContext` below, so each `boundaryPort` denotes a real
     * port instance and `boundaryPort.portNumber` denotes a real value. The
     * feature chains follow the TA assembly exactly:
     *
     *     fsim.pcs.threadFlightSim.ioManager   BackgroundIOManager
     *     fsim.pcs.threadPhysics               ThreadPhysics
     *     fsim.simbus.pcsNode / .seNode        BusOrchestrator nodes
     *     fsim.sem                             SyntheticEnvironmentMgr
     *     fsim.fgfs.visualEngine / .localHttpServer
     *     fsim.hotas, fsim.osm
     */

    requirement def PortAssignmentReq {
        doc /* The communication port shall match the assigned port number.
         * `boundaryPort` is the subject: binding it is what makes the
         * constraint evaluable. */
        subject boundaryPort : UdpPort;
        attribute expectedPort : Integer;
        attribute actualPort : Integer = boundaryPort.portNumber;
        require constraint { actualPort == expectedPort }
    }

    requirement def HttpPortAssignmentReq {
        doc /* The HTTP endpoint shall match the assigned port number.
         * A separate def because these are HttpPort, not UdpPort. */
        subject boundaryPort : HttpPort;
        attribute expectedPort : Integer;
        attribute actualPort : Integer = boundaryPort.portNumber;
        require constraint { actualPort == expectedPort }
    }

    requirement def HostAssignmentReq {
        doc /* The endpoint shall address the assigned host.
         *
         * NOTE: this depends on `address` being typed. fsim_TA_Ports.sysml
         * currently declares it untyped (`attribute address;`), so
         * `actualAddress : String = boundaryPort.address` narrows an
         * untyped feature to String. See the correction proposed for
         * fsim_TA_Ports.sysml. */
        subject boundaryPort : HttpsPort;
        attribute expectedAddress : String;
        attribute actualAddress : String = boundaryPort.address;
        require constraint { actualAddress == expectedAddress }
    }

    // ---- the assembly the subjects are taken from ------------------------
    part ifContext {
        doc /* Holds the one FlightSimSystem usage every requirement below
         * binds its subject through. */
        part fsim : FlightSimulator_TA_System::FlightSimSystem;

        // ---- HOTAS -> PCS, 5005 ------------------------------------------
        requirement req_hotasControlOutPort : PortAssignmentReq {
            doc /* HOTAS -> PCS. Source end of the link. */
            subject boundaryPort = fsim.hotas.e_hotasControlOut;
            :>> expectedPort = 5005;
        }

        requirement req_pcsHotasControlInPort : PortAssignmentReq {
            doc /* HOTAS -> PCS. Sink end; both ends must agree. */
            subject boundaryPort = fsim.pcs.threadFlightSim.ioManager.e_hotasControlIn;
            :>> expectedPort = 5005;
        }

        // ---- PCS -> orchestrator, 5006 -----------------------------------
        requirement req_pcsOwnshipStateOutPort : PortAssignmentReq {
            doc /* PCS -> orchestrator, source end. */
            subject boundaryPort = fsim.pcs.threadFlightSim.ioManager.e_ownshipStateOut;
            :>> expectedPort = 5006;
        }

        requirement req_orchOwnshipStateInPort : PortAssignmentReq {
            doc /* PCS -> orchestrator, sink end. */
            subject boundaryPort = fsim.simbus.pcsNode.e_ownshipStateIn;
            :>> expectedPort = 5006;
        }

        // ---- orchestrator -> SEM, 5007 -----------------------------------
        requirement req_orchOwnshipStateOutPort : PortAssignmentReq {
            doc /* orchestrator -> SEM, source end. */
            subject boundaryPort = fsim.simbus.seNode.e_ownshipStateOut;
            :>> expectedPort = 5007;
        }

        requirement req_semOwnshipStateInPort : PortAssignmentReq {
            doc /* orchestrator -> SEM, sink end. This link carries
             * OwnshipState, NOT radar data - the original name
             * req_semRadarPort misdescribed it and could have led an
             * integrator to wire the federation backwards. The SEM's radar
             * output is 6007, below. */
            subject boundaryPort = fsim.sem.e_ownshipStateIn;
            :>> expectedPort = 5007;
        }

        // ---- SEM -> orchestrator, 6007 -----------------------------------
        requirement req_semRadarDetectionsOutPort : PortAssignmentReq {
            doc /* SEM -> orchestrator, source end. */
            subject boundaryPort = fsim.sem.e_radarDetectionsOut;
            :>> expectedPort = 6007;
        }

        requirement req_orchRadarDetectionsInPort : PortAssignmentReq {
            doc /* SEM -> orchestrator, sink end. */
            subject boundaryPort = fsim.simbus.seNode.e_radarDetectionsIn;
            :>> expectedPort = 6007;
        }

        // ---- orchestrator -> PCS, 6006 -----------------------------------
        requirement req_orchRadarDetectionsOutPort : PortAssignmentReq {
            doc /* orchestrator -> PCS, source end. */
            subject boundaryPort = fsim.simbus.pcsNode.e_radarDetectionsOut;
            :>> expectedPort = 6006;
        }

        requirement req_pcsRadarDetectionsInPort : PortAssignmentReq {
            doc /* orchestrator -> PCS, sink end. */
            subject boundaryPort = fsim.pcs.threadFlightSim.ioManager.e_radarDetectionsIn;
            :>> expectedPort = 6006;
        }

        // ---- PCS physics -> FlightGear, 5550 -----------------------------
        requirement req_pcsNativeFdmOutPort : PortAssignmentReq {
            doc /* FGNetFDM v24, source end. */
            subject boundaryPort = fsim.pcs.threadPhysics.e_nativeJSBSimFdmOut;
            :>> expectedPort = 5550;
        }

        requirement req_fgNativeFdmInPort : PortAssignmentReq {
            doc /* FGNetFDM v24, sink end. */
            subject boundaryPort = fsim.fgfs.visualEngine.e_nativeFdmIn;
            :>> expectedPort = 5550;
        }

        // ---- FlightGear httpd -> PCS, 8080 -------------------------------
        requirement req_fgHttpdOutPort : HttpPortAssignmentReq {
            doc /* FlightGear httpd, ground elevation, source end. */
            subject boundaryPort = fsim.fgfs.localHttpServer.e_groundElevMeterOut;
            :>> expectedPort = 8080;
        }

        requirement req_pcsGroundElevInPort : HttpPortAssignmentReq {
            doc /* FlightGear httpd, sink end. */
            subject boundaryPort = fsim.pcs.threadFlightSim.ioManager.e_groundElevMeterIn;
            :>> expectedPort = 8080;
        }

        // ---- external tile service ---------------------------------------
        requirement req_osmTileHost : HostAssignmentReq {
            doc /* OpenStreetMap tile service, consumed by the Tactical2DMap. */
            subject boundaryPort = fsim.osm.e_tile2DInterface;
            :>> expectedAddress = "tile.openstreetmap.org";
        }
    }
}
