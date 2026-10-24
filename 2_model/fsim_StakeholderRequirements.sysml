package FlightSimulator_StakeholderRequirements {
    doc /* STAKEHOLDER-LEVEL requirements: one per use case, plus one
     * realization constraint that has no use case, each stating the need in
     * the stakeholder's terms. This layer sits ABOVE the L1/L2 baseline held
     * in the design workbook (fsim_Design_v5.xlsx) and emitted from it into
     * fsim_SystemRequirements.sysml.
     *
     * What this package contributes is the derivation of the L1 baseline in section 3.
     *
     * The package dependency runs one way only:
     *
     *     UCvsLA  ->  StakeholderRequirements  ->  SystemRequirements
     *
     * Neither file imports the other; both use fully qualified names, which
     * resolve against the project load path and cannot form a cycle.
     */

    public import ScalarValues::*;
    private import RequirementDerivation::*;

    // NO wildcard imports of the project packages, and every cross-package
    // reference below is fully qualified.

    // ---------------------------------------------------
    // 1. Requirement Definitions (The Blueprints)
    // ---------------------------------------------------

    requirement def StakeholderRequirement {
        doc /* Common shape of every stakeholder requirement below. The
         * subject is the system of interest, taken from the LOGICAL
         * architecture and treated as a black box, so that no stakeholder
         * statement names an internal block. Declared once here: if the
         * system-of-interest boundary ever moves, this is the only line
         * that changes. */
        subject fsim : FlightSimulator_LogicalArchitecture::FlightSimSystem;
    }

    requirement def ManualFlightReq :> StakeholderRequirement {
        doc /* The system shall process manual flight controls from the HOTAS to the physics engine. */
    }

    requirement def NavigationMapReq :> StakeholderRequirement {
        doc /* The system shall display 2D map tiles fetched from an external provider (e.g. OpenStreetMap) for tactical navigation. */
    }

    requirement def ThreatAssessmentReq :> StakeholderRequirement {
        doc /* The system shall receive and display radar detections from the Synthetic Environment. */
    }

    requirement def ScenarioManagementReq :> StakeholderRequirement {
        doc /* The system shall allow an instructor to inject and remove radar beacons dynamically via a GUI. */
    }

    requirement def DatabusManagementReq :> StakeholderRequirement {
        doc /* The system shall synchronize state across the distributed
         * sub-systems so that every process observes one coherent
         * simulation state. Restates UC_DatabusManagement. */
    }

    requirement def ExternalIntegrationReq :> StakeholderRequirement {
        doc /* The system shall integrate the external COTS components it
         * depends on - the FlightGear visual engine and the OpenStreetMap
         * tile service - rather than reimplementing them. Realized by
         * L1SYS-REQ-05; it has no use case of its own because it is a
         * constraint on realization, not a user-visible capability. */
    }

    // ---------------------------------------------------
    // 2. Requirement Usages
    // ---------------------------------------------------
    // These carry NO subject of their own - they inherit the black-box
    // subject from StakeholderRequirement.

    requirement req_manualFlight : ManualFlightReq;
    requirement req_navigationMap : NavigationMapReq;
    requirement req_threatAssessment : ThreatAssessmentReq;
    requirement req_scenarioManagement : ScenarioManagementReq;
    requirement req_databusManagement : DatabusManagementReq;
    requirement req_externalIntegration : ExternalIntegrationReq;

    // ---------------------------------------------------
    // 3. Derivation of the L1 baseline
    // ---------------------------------------------------
    // Expressed with `#derivation connection`, the form used by both
    // supplied reference examples (RequirementDerivationExample and
    // VehicleRequirementDerivation). The stakeholder requirement is the
    // #original; the L1 requirements it gives rise to are the #derive
    // ends - the same direction as vehicleMassRequirement (#original)
    // deriving chassisMassRequirement and engineMassRequirement.
    //
    // References use the STABLE ALIASES (L1SYS_REQ_nn) emitted by the
    // generator, not the derived element names. A workbook rewording
    // changes the derived names; the alias is the workbook ID and does
    // not move.
    //
    // An earlier draft used `dependency ... from ... to ...` here. That
    // was the wrong edge kind: it expresses an untyped dependency, not a
    // derivation, so no tool would read it as traceability.

    #derivation connection {
        end #original ::> req_manualFlight;
        end #derive ::> FlightSimulator_SystemRequirements::L1SYS_REQ_01;
    }

    // L1SYS-REQ-02 carries TWO stakeholder needs, so it appears as a
    // #derive end of both. That is legitimate but it is the one place the
    // chain is not 1:1 - see the note at the end of this package.
    #derivation connection {
        end #original ::> req_navigationMap;
        end #derive ::> FlightSimulator_SystemRequirements::L1SYS_REQ_02;
    }

    #derivation connection {
        end #original ::> req_threatAssessment;
        end #derive ::> FlightSimulator_SystemRequirements::L1SYS_REQ_02;
    }

    #derivation connection {
        end #original ::> req_scenarioManagement;
        end #derive ::> FlightSimulator_SystemRequirements::L1SYS_REQ_03;
    }

    #derivation connection {
        end #original ::> req_databusManagement;
        end #derive ::> FlightSimulator_SystemRequirements::L1SYS_REQ_04;
    }

    #derivation connection {
        end #original ::> req_externalIntegration;
        end #derive ::> FlightSimulator_SystemRequirements::L1SYS_REQ_05;
    }

    // NOTE: L1SYS-REQ-02 ("geographic positioning AND threat detection
    // awareness") carries two distinct stakeholder needs, which is why its
    // L2 children split into the 02.01 map group and the 02.03/02.04
    // threat group. Splitting it in the workbook would make the chain 1:1
    // throughout; that is a workbook decision, so it is recorded here
    // rather than applied.
}