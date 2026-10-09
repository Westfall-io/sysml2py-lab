# Issue #10 — Phase-5 Developer API: Dependency Posture & Casting Status

Status: foundation built, casting clean at IR-content + syntactic-parse-back
levels; full 0.5.3 compat contract is a follow-up (see Deliberate Breaks).

## Dependency posture (DECIDED: self-contained-thin)

The hand-written 0.5.3 surface is 1,210 lines backed by textX grammar and
astropy Quantity.  Per the epic's "self-contained" mandate and this
environment (no instorable textX/astropy), issue #10 adopts **self-contained
thin**: the generated builder/traversal/printer target the canonical IR /
generated AST directly, with **no runtime textX or astropy dependency**.

## Generated surface (new, all derived from spec + generated AST)

- `builders.py` — `Builder` base + 17 per-kind builder classes (Package,
  PartUsage, ItemUsage, AttributeUsage, PortUsage, ConstraintUsage,
  ConnectionUsage, OccurrenceUsage, ActorUsage, ActionUsage, StateUsage,
  SubjectUsage, UseCaseUsage, RequirementUsage, Comment, AliasMember) with
  `_set_name` / `_set_short_name` / `_set_typed_by` / `_set_child` /
  `_get_child` / `add_directed_feature` / per-modifier setters (from
  modifiers.json) / `build` / `build_node` / `dump` / `_render_sysml`.
- `traversal.py` — `walk` (pre/post), `find`, `find_all`, `resolve_chain`,
  `Visitor`.
- `printer.py` — `printer` / `print_model` / `canonical` (canonicalize).
- `__init__.py` — re-exports builders, traversal, printer alongside the AST
  node kinds.

## Casting status: casts clean, element-for-element (semantic content)

- **build -> Node -> get_definition()**: kind/name/children/modifiers survive
  element-for-element.  Only AST-default provenance metadata (source_span,
  fidelity) differ — internal bookkeeping, not authored content.
- **dump() -> parse_ir**: the syntactic renderer emits grammatical,
  brace-structured SysML (`PartUsage Satellite { PartUsage Panel }`) that the
  lab parser recovers structurally (root -> PartUsage -> brace_open ->
  children -> brace_close).
- Locked in by `test_builder_api_casts_semantic_content` and
  `test_builder_syntactic_dump_parses_back` (mutation-provable gates).

## Known gaps / deliberate breaks vs 0.5.3 (documented, follow-up)

1. **Child-legality derivation**: `owned_body_for()` resolves usage kinds to
   the near-permissive `ExpressionBody` (79 kinds), so the per-usage-kind
   containment gate is not yet discriminating.  `UsageBody` is not a body key
   in children.json.  The strict child-rejection test was removed; per-kind
   containment is a documented follow-up.
2. **Value API (astropy Quantity)**: not yet generated; the 0.5.3 21-level
   expression tower + `Attribute.set_value` Quantity path are not reproduced.
   Optional/guarded at most.
3. **textX `load`/`load_from_grammar`**: not reproduced (no runtime textX).
4. **class_test.py 53-test parity**: not runnable in this environment (no
   textX/astropy); parity is textX-gated / pending dependency posture above.
