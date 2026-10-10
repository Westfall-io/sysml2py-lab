package 'Part Definition Example' {
    	public import ScalarValues::*;

    	part def Vehicle {
    		attribute mass : Real;
    		attribute status : VehicleStatus;

    		part eng : Engine;

    		ref part driver : Person;
    	}

    	attribute def VehicleStatus {
    		gearSetting : Integer;
    		acceleratorPosition : Real;
    	}

    	part def Engine;
    	part def Person;
    }