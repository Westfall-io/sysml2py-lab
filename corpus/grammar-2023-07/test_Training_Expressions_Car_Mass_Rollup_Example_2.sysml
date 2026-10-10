package 'Car Mass Rollup 1' {
    	public import ScalarValues::*;
    	public import MassRollup2::*;
    	
    	part def CarPart :> MassedThing {			
    		attribute serialNumber: String;
    	}
    	
    	part car: CarPart :> compositeThing {	
    		attribute vin :>> serialNumber;
    		
    		part carParts: CarPart[*] :>> subcomponents;
    		
    		part engine :> carParts;
    		
    		part transmission :> carParts;
    	}
    	
    	public import SI::kg;
    	part c :> car {
    		attribute :>> simpleMass = 1000[kg];
    		part :>> engine {
    			attribute :>> simpleMass = 100[kg];
    		}
    		
    		part redefines transmission {
    			attribute :>> simpleMass = 50[kg];
    		}	
    	}
    }