# classes.py parity report (issue #6)

Compares the grammar-derived children/body model against the current
hand-curated `sysml2py/grammar/classes.py` (266 classes, 265 `dump()`,
72 `get_definition()`, 73 `NotImplementedError` sites).

## Missing definition kinds (implemented-but-unmodelled)

The grammar's `DefinitionElement` allows these kinds, but `classes.py`'s
`DefinitionElement` dispatch does not handle them:

| Kind | Class in classes.py |
| --- | --- |
| ActionDefinition | yes |
| AllocationDefinition | **NO** |
| AnalysisCaseDefinition | yes |
| AnnotatingElement | yes |
| AttributeDefinition | yes |
| CalculationDefinition | yes |
| CaseDefinition | **NO** |
| ConcernDefinition | **NO** |
| ConnectionDefinition | yes |
| ConstraintDefinition | yes |
| Dependency | **NO** |
| EnumerationDefinition | yes |
| ExtendedDefinition | **NO** |
| FlowConnectionDefinition | yes |
| IndividualDefinition | **NO** |
| InterfaceDefinition | yes |
| ItemDefinition | yes |
| LibraryPackage | **NO** |
| MetadataDefinition | **NO** |
| OccurrenceDefinition | **NO** |
| Package | yes |
| PartDefinition | yes |
| PortDefinition | yes |
| RenderingDefinition | **NO** |
| RequirementDefinition | yes |
| StateDefinition | yes |
| UseCaseDefinition | **NO** |
| VerificationCaseDefinition | **NO** |
| ViewDefinition | **NO** |
| ViewpointDefinition | **NO** |

### Issue-mandated missing kinds

| Missing definition kind | In classes.py dispatch? |
| --- | --- |
| UseCaseDefinition | **MISSING** |
| OccurrenceDefinition | **MISSING** |
| ViewDefinition | **MISSING** |
| VerificationCaseDefinition | **MISSING** |
| MetadataDefinition | **MISSING** |

## Modelled kinds (this model's child-kind vocabulary)

120 distinct element kinds are modeled across all bodies:

```
ActionDefinition ActionTargetSuccession ActionUsage ActorUsage AliasMember AllocationDefinition AllocationUsage AnalysisCaseDefinition AnalysisCaseUsage AnnotatingMember AssertConstraintUsage Association AssociationStructure AttributeDefinition AttributeUsage Behavior BindingConnector BodyParameter CalculationDefinition CalculationUsage CaseDefinition CaseUsage Class Classifier Comment ConcernDefinition ConcernUsage Conjugation ConnectionDefinition ConnectionUsage ConstraintDefinition ConstraintUsage DataType DefaultInterfaceEnd DefaultReferenceUsage Dependency Disjoining Documentation EmptyParameterMember EnumeratedValue EnumerationDefinition EnumerationUsage EventOccurrenceUsage ExhibitStateUsage Expose ExtendedDefinition ExtendedUsage FeatureInverting FeatureMember FeatureTyping FlowConnectionDefinition FlowConnectionUsage FramedConcernUsage Function GuardedSuccessionMember Import IncludeUseCaseUsage IndividualDefinition IndividualUsage Interaction InterfaceDefinition InterfaceUsage ItemDefinition ItemUsage LibraryPackage Message Metaclass MetadataBodyFeatureMember MetadataBodyUsageMember MetadataDefinition MetadataUsage Multiplicity Namespace NamespaceMember ObjectiveRequirementUsage OccurrenceDefinition OccurrenceUsage OwnedExpression OwnedReferenceSubsetting Package PartDefinition PartUsage PerformActionUsage PortDefinition PortUsage PortionUsage Predicate Redefinition ReferenceUsage RenderingDefinition RenderingUsage RequirementConstraintUsage RequirementDefinition RequirementUsage RequirementVerificationUsage SatisfyRequirementUsage Specialization StakeholderUsage StateActionUsage StateDefinition StateUsage Structure Subclassification SubjectUsage Subsetting Succession SuccessionFlowConnectionUsage TextualRepresentation TransitionSourceMember Type TypeFeaturing UseCaseDefinition UseCaseUsage VariantReference VerificationCaseDefinition VerificationCaseUsage ViewDefinition ViewUsage ViewpointDefinition ViewpointUsage
```

