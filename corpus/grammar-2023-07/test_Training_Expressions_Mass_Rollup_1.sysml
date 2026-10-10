package MassRollup1 {
    	public import NumericalFunctions::*;
    	
    	part def MassedThing {
    		attribute simpleMass :> ISQ::mass; 
    		attribute totalMass :> ISQ::mass;
    	}
    	
    	part simpleThing : MassedThing {
    		attribute :>> totalMass = simpleMass;
    	}
    	
    	part compositeThing : MassedThing {
    		part subcomponents: MassedThing[*];		
    		attribute :>> totalMass =
    			simpleMass + sum(subcomponents.totalMass); 
    	}
    	
    }