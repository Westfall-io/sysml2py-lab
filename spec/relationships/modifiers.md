# Modifier model — per node kind (issue #5)

| Prefix/kind | Modifier | Tokens | Kind | Card | Mutually-exclusive-with | Source |
| --- | --- | --- | --- | --- | --- | --- |
| BasicDefinitionPrefix | isAbstract | abstract | flag |  | isVariation | BasicDefinitionPrefix:467 |
| BasicDefinitionPrefix | isVariation | variation | flag |  | isAbstract | BasicDefinitionPrefix:467 |
| BasicUsagePrefix | direction | in, out, inout | enum | ? |  | RefPrefix:533 |
| BasicUsagePrefix | isAbstract | abstract | flag | ? | isVariation | RefPrefix:534 |
| BasicUsagePrefix | isVariation | variation | flag | ? | isAbstract | RefPrefix:534 |
| BasicUsagePrefix | isReadOnly | readonly | flag | ? |  | RefPrefix:535 |
| BasicUsagePrefix | isDerived | derived | flag | ? |  | RefPrefix:536 |
| BasicUsagePrefix | isEnd | end | flag | ? |  | RefPrefix:537 |
| BasicUsagePrefix | isReference | ref | flag | ? |  | BasicUsagePrefix:542 |
| DefinitionPrefix | isAbstract | abstract | flag |  | isVariation | BasicDefinitionPrefix:467 |
| DefinitionPrefix | isVariation | variation | flag |  | isAbstract | BasicDefinitionPrefix:467 |
| FeatureDirection | in | in | enum |  | out, inout | FeatureDirection:529 |
| FeatureDirection | out | out | enum |  | in, inout | FeatureDirection:529 |
| FeatureDirection | inout | inout | enum |  | in, out | FeatureDirection:529 |
| MemberPrefix | visibility | public, private, protected | enum | ? |  | MemberPrefix:214 |
| OccurrenceDefinitionPrefix | isAbstract | abstract | flag |  | isVariation | BasicDefinitionPrefix:467 |
| OccurrenceDefinitionPrefix | isVariation | variation | flag |  | isAbstract | BasicDefinitionPrefix:467 |
| OccurrenceDefinitionPrefix | isIndividual | individual | flag | ? |  | OccurrenceDefinitionPrefix:785 |
| OccurrenceUsagePrefix | direction | in, out, inout | enum | ? |  | RefPrefix:533 |
| OccurrenceUsagePrefix | isAbstract | abstract | flag | ? | isVariation | RefPrefix:534 |
| OccurrenceUsagePrefix | isVariation | variation | flag | ? | isAbstract | RefPrefix:534 |
| OccurrenceUsagePrefix | isReadOnly | readonly | flag | ? |  | RefPrefix:535 |
| OccurrenceUsagePrefix | isDerived | derived | flag | ? |  | RefPrefix:536 |
| OccurrenceUsagePrefix | isEnd | end | flag | ? |  | RefPrefix:537 |
| OccurrenceUsagePrefix | isReference | ref | flag | ? |  | BasicUsagePrefix:542 |
| OccurrenceUsagePrefix | isIndividual | individual | flag | ? |  | OccurrenceUsagePrefix:815 |
| OccurrenceUsagePrefix | portionKind | snapshot, timeslice | enum | ? |  | OccurrenceUsagePrefix:816 |
| PortionKind | snapshot | snapshot | enum |  | timeslice | PortionKind:837 |
| PortionKind | timeslice | timeslice | enum |  | snapshot | PortionKind:837 |
| RefPrefix | direction | in, out, inout | enum | ? |  | RefPrefix:533 |
| RefPrefix | isAbstract | abstract | flag | ? | isVariation | RefPrefix:534 |
| RefPrefix | isVariation | variation | flag | ? | isAbstract | RefPrefix:534 |
| RefPrefix | isReadOnly | readonly | flag | ? |  | RefPrefix:535 |
| RefPrefix | isDerived | derived | flag | ? |  | RefPrefix:536 |
| RefPrefix | isEnd | end | flag | ? |  | RefPrefix:537 |
| UsagePrefix | direction | in, out, inout | enum | ? |  | RefPrefix:533 |
| UsagePrefix | isAbstract | abstract | flag | ? | isVariation | RefPrefix:534 |
| UsagePrefix | isVariation | variation | flag | ? | isAbstract | RefPrefix:534 |
| UsagePrefix | isReadOnly | readonly | flag | ? |  | RefPrefix:535 |
| UsagePrefix | isDerived | derived | flag | ? |  | RefPrefix:536 |
| UsagePrefix | isEnd | end | flag | ? |  | RefPrefix:537 |
| UsagePrefix | isReference | ref | flag | ? |  | BasicUsagePrefix:542 |
| VisibilityIndicator | public | public | enum |  | private, protected | VisibilityIndicator:293 |
| VisibilityIndicator | private | private | enum |  | public, protected | VisibilityIndicator:293 |
| VisibilityIndicator | protected | protected | enum |  | public, private | VisibilityIndicator:293 |
