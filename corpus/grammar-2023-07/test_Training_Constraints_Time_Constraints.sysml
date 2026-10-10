package 'Time Constraints' {
    	public import ISQ::TemperatureValue;
    	public import ISQ::DurationValue;
    	public import Time::TimeInstantValue;
    	public import Time::TimeOf;
    	public import Time::DurationOf;
    	public import SI::h;
    	public import SI::s;

    	attribute def MaintenanceDone;
    	
    	part def Vehicle {
    		attribute maintenanceTime : TimeInstantValue;
    		attribute maintenanceInterval : DurationValue;
    		attribute maxTemperature : TemperatureValue;
    	}
    	
    	state healthStates {
    		in vehicle : Vehicle;
    		
    		entry; then normal;
    		
    		state normal;
    		accept at vehicle.maintenanceTime
    			then maintenance;
    		
    		state maintenance {
    			assert constraint { TimeOf(maintenance) > vehicle.maintenanceTime }
    			assert constraint { TimeOf(maintenance) - TimeOf(normal.done) < 2 [s] }
    			entry assign vehicle.maintenanceTime := vehicle.maintenanceTime + vehicle.maintenanceInterval;
    		}
    		accept MaintenanceDone
    			then normal;
    		
    		constraint { DurationOf(maintenance) <= 48 [h] }
    	}
    }