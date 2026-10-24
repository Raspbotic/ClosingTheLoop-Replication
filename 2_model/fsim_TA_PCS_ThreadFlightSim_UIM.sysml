package FlightSimulator_TA_PCS_ThreadFlightSim_UIManager {
    public import ScalarValues::*;
    public import FlightSimulator_TA_Ports::PortDefinitions::*;

    part def UserInterfaceManager {
        doc /* Handles all frontend Qt GUI interactions, cockpit displays, and SSF controls */
        
        // Flight & Cockpit Actions
        action controlUserInputs {
        	action controlManualFlight;
	        action controlFlightSystems;
	        action controlMissionSettings;
	        action controlNavigationAP;
	        action controlNavigationAH;
	        action controlNavigationAT;	      
        }
      	action updateCockpitDisplays;
      	action updateAttentionGetters;
      		doc /* evaluate the ground-proximity condition from height above
      		 * ground and vertical speed, and drive the attention-getter
      		 * strip: display the warning (e.g. PULL UP) flashing
      		 * intermittently while the condition persists, blank otherwise.
      		 * Realizes L2SYS-REQ-02.04. */

        // Simulation Specific Functions (SSF)
        action manageSSF {
        	action ssfStartSimulation;
        		doc /* start or resume the simulation loop (e.g., telemetry reading, flight_gear physics model, flight_gear otw (out of the window) scenario, hotas inputs, synthetic environment data )  */
	        action ssfStopSimulation;
        		doc /* pause the simulation loop  */
	        action ssfResetSimulation;
        		doc /* reload simulation's initial conditions (e.g. aircraft at base runaway). 	        	        	        
        		* caveat: can be applied only during a flight-freeze condition, i.e. after a ssfStopSimulation has been executed */	           
	        action ssfLaunchOtwView;
        		doc /* launches flight_gear app, loading the specified aircraft type (e.g. f16) at the currently selected airport (either default choice or as per ssfSelectAirport) */	        
	        action ssfSetOwnPosition;
        		doc /* set lat, long, altitude of the ownship 	        	        	        
        		* caveat: can be applied only during a flight-freeze condition, i.e. after a ssfStopSimulation has been executed */	           
	        action ssfSaveSnapshot;
        		doc /* save all state-variables of the simulation, incl: physics, beacon list, map view, otw view, etc ...  	        	        	        
        		* caveat: can be applied only during a flight-freeze condition, i.e. after a ssfStopSimulation has been executed */	   	        
	        action ssfLoadSnapshot;
        		doc /* load all state-variables of the simulation as specified in the chosen snapshot file	        	        	        
        		* caveat: can be applied only during a flight-freeze condition, i.e. after a ssfStopSimulation has been executed */	        
	        action ssfFreezeFuelConsupmtion;
        		doc /* hold tank contents constant */	        	        	        
	        action ssfLogTelemetry;
        		doc /* log telemetry to a csv file. Fields are:	        	        	        
        		 * wall_time_iso, sim_time_s, model, lat_deg, lon_deg. alt_m, agl_m, 
        		 * v_speed_mps, speed_mps, heading_deg, target_heading_deg. pitch_deg, pitch_rate_dps, roll_deg, target_alt_m, target_speed_mps, 
        		 * aileron_cmd, elevator_cmd, rudder_cmd, throttle_cmd, gear_cmd, trim_pitch, trim_roll, trim_yaw, 
        		 * ap_engaged, ah_engaged, at_engaged, warning_state
        		 */	        	      
			action ssfSelectAirport;
			    doc /* select the active airport (ICAO) from a configurable list; sets the
			     * ownship ground-start initial condition and the OTW launch location.
			     * caveat: can be applied only during a flight-freeze condition */  	        
        }
		        
        // Submodule Boundary Ports
        port i_OwnTelemetryIn : InternalSignalPort;
        port i_HotasControlOut : InternalSignalPort;
        port i_2dTileIn : InternalSignalPort;
        port i_TileCachePathOut : InternalSignalPort;
        
        // NEW: Output ports to feed the Map Window
        port i_OwnStateOut : InternalSignalPort;
        port i_BeaconDataOut : InternalSignalPort;
    }
}