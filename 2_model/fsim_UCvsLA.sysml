package FlightSimulator_UseCases {
    doc /* Use cases, their subjects in the LOGICAL architecture, and the
     * restatement edges up to the stakeholder requirements.
     *
     * Two changes from the previous version:
     *
     * 1. SUBJECTS no longer navigate through `FlightSimSystem::`.
     *    `part def FlightSimSystem` in fsim_LA.sysml owns the part USAGES
     *    pilotControlStation / busManager / virtualOwnship - not the defs.
     *
     * 2. The five `restates_UC_*` dependencies MOVED HERE from
     *    fsim_StakeholderRequirements.sysml, so the use-case view owns its
     *    own link to the stakeholder layer instead of depending on another
     *    file being loaded for the link to exist. The package dependency now
     *    runs one way only:
     *
     *        UCvsLA  ->  StakeholderRequirements  ->  SystemRequirements
     */

    public import ScalarValues::*;
    public import UseCases::*;

    // Use Case 01: Manual Flight Control
    use case def UC_ManualFlight {
        subject pcs : FlightSimulator_LogicalArchitecture::PilotControlStation;
        objective obj_performManualFlight;
        actor pilot;
    }

    // Use Case 02: Tactical Awareness & Navigation
    use case def UC_TacticalNavigation {
        subject tmap2D : FlightSimulator_LogicalArchitecture::Tactical2DMap;
        objective obj_navigateWithMap;
        actor pilot;
    }

    // Use Case 03: Threat Assessment
    use case def UC_ThreatAssessment {
        subject tmap2D : FlightSimulator_LogicalArchitecture::Tactical2DMap;
        objective obj_assessThreats;
        actor pilot;
    }

    // Use Case 04: Scenario Management
    use case def UC_BeaconInjection {
        subject syntEnv : FlightSimulator_LogicalArchitecture::SyntheticEnvironment;
        objective obj_manageBeacons;
        actor instructor;
    }

    // Use Case 05: Data Synchronization
    use case def UC_DatabusManagement {
        subject busManager : FlightSimulator_LogicalArchitecture::BusManager;
        objective obj_manageBusComms;
        actor simBus;
    }

    // ---------------------------------------------------
    // Restatement: stakeholder requirement <- use case
    // ---------------------------------------------------
    // Each stakeholder requirement restates exactly one use case. Direction
    // is unchanged from when these lived in the stakeholder package: the
    // requirement is the client, the use case the supplier. Ownership is
    // what moved, not semantics.
    //
    // The targets are the stakeholder requirement USAGES, the same elements
    // the #derivation connections in fsim_StakeholderRequirements.sysml
    // subset, so a use case and the L1 baseline hang off one element each.
    //
    // req_externalIntegration has no edge here on purpose: it is a
    // constraint on realization, not a user-visible capability, so it has no
    // use case. All five use cases below are covered exactly once.

    dependency restates_UC_ManualFlight
        from FlightSimulator_StakeholderRequirements::req_manualFlight
        to   UC_ManualFlight;

    dependency restates_UC_TacticalNavigation
        from FlightSimulator_StakeholderRequirements::req_navigationMap
        to   UC_TacticalNavigation;

    dependency restates_UC_ThreatAssessment
        from FlightSimulator_StakeholderRequirements::req_threatAssessment
        to   UC_ThreatAssessment;

    dependency restates_UC_BeaconInjection
        from FlightSimulator_StakeholderRequirements::req_scenarioManagement
        to   UC_BeaconInjection;

    dependency restates_UC_DatabusManagement
        from FlightSimulator_StakeholderRequirements::req_databusManagement
        to   UC_DatabusManagement;
}