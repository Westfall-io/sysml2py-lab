# Issue #10 — Phase-5 Developer API: Dependency Posture & Casting Status

Status: foundation built + one review cycle (r1: REQUEST CHANGES → fixed →
re-review launched).  Casting clean at IR-content + syntactic-parse-back
levels; full 0.5.3 compat contract is a follow-up (see Deliberate Breaks).

## Dependency posture (DECIDED: self-contained-thin)

The hand-written 0.5.3 surface is 1,210 lines backed by textX grammar and
astropy Quantity.  Per the epic's "self-contained" mandate and this
environment (no instorable textX/astropy), issue #10 adopts **self-contained
thin**: the generated builder/traversal/printer target the canonical IR /
generated AST directly, with **no runtime textX or astropy dependency**.

## Generated surface (new, all derived from spec + generated AST)

- `builders.py` — `Builder` base + per-kind builder classes derived from
  `IR_KIND_ALIASES` minus a deny-list (`BUILDER_DENY`), so coverage cannot
  drift from the alias table (W2-18).  Currently 22: Package, PartUsage,
  ItemUsage, AttributeUsage, PortUsage, ConstraintUsage, ConnectionUsage,
  OccurrenceUsage, ActorUsage, ActionUsage, StateUsage, SubjectUsage,
  UseCaseUsage, RequirementUsage, InterfaceUsage, Message,
  ObjectiveRequirementUsage, Succession, TransitionUsage, Comment, AliasMember
  (DefaultReferenceUsage is the internal directed-feature default).  Methods:
  `_set_name` / `_set_short_name` / `_set_typed_by` / `_set_child` /
  `_get_child` / `add_directed_feature` / per-modifier setters (from
  modifiers.json BasicUsagePrefix) / `build` / `build_node` / `dump` /
  `_render_sysml`.
- `traversal.py` — `walk` (pre/post), `find`, `find_all`, `resolve_chain`,
  `Visitor` (genuine pre/post-order).
- `printer.py` — `print_model` / `canonical` (canonicalize-based).  Routes
  builder-built trees through `_render_sysml`; the `printer` submodule name is
  NOT shadowed (W2-20).  Reached as `sysml2py.printer` (the module).
- `__init__.py` — re-exports builders, traversal, printer functions.

## Casting status: casts clean, element-for-element (semantic content)

- **build -> Node -> get_definition()**: kind/name/children/modifiers survive
  element-for-element, asserted against a hand-written expected IR literal
  (W1-8) so a dropped field fails.  `_set_typed_by` emits `type_refs` (the
  AST IR key, B1); defaults differ only in AST-internal provenance metadata
  (source_span, fidelity).
- **dump()/print_model -> parse_ir**: `_render_sysml` emits brace-structured
  textual SysML that the lab parser recovers structurally; the recovered shape
  (child name inside the brace owner) is asserted (W1-9).
- Locked in by phase-5 cast/behaviour tests (semantic, typed_by, syntactic,
  coverage, behaviour, definition-cannot-be-typed).

## Review-cycle facts & fixes (r1)

r1 review (REQUEST CHANGES) found 3 blockers + 7 W1 + 10 W2 — all genuine.
Fixed:
- **B1** `_set_typed_by` wrote a non-existent `typed_by` IR key (throw); now
  emits `type_refs`.  Dropped dead `definition` key.
- **B2** child-legality gate promoted a non-load-bearing resolver to a hard
  raise that rejected legal models; now ADVISORY (no hard raise) until the
  per-kind containment resolver is pinned.
- **B3** `_is_definition` set in `__init__` shadowed the generated class flag;
  now a class attribute (default False, package True).  `:>` uses `typed_by`
  (not self-specialization).
- **W1-4/5** usage modifiers now rendered on all builders; value/enum
  modifiers keep their value (`k=v`) in `_to_ir`.
- **W1-6** `add_directed_feature` kind_name lookup keyed by node-kind name.
- **W1-7** printer routes builder trees through `_render_sysml`.
- **W1-8/9/10** cast tests hardened + behavioural coverage added.
- **W2** Visitor post-order, `_walk_chain` self-name, dead `_set_child`
  override removed, printer shadow, UPSERT_CHILD_KINDS→frozenset, coverage
  pinned to IR_KIND_ALIASES, `_render_sysml` claim tempered (no `;`, no
  grammar keywords).

## Known gaps / deliberate breaks vs 0.5.3 (documented, follow-up)

1. **Child-legality derivation**: ADVISORY only — `owned_body_for()` resolves
   usage kinds broadly; `UsageBody` is not a children.json body key.  A strict
   per-kind containment gate is a follow-up.
2. **Value API (astropy Quantity)**: not yet generated; the 0.5.3 21-level
   expression tower + `Attribute.set_value` Quantity path are not reproduced.
3. **textX `load`/`load_from_grammar`**: not reproduced (no runtime textX).
4. **class_test.py 53-test parity**: not runnable in this environment (no
   textX/astropy); textX-gated / pending dependency posture.
5. **`_render_sysml` is not fully grammatical SysML**: emits class names (not
   grammar keywords), no `;` terminators, no `short_name`.  The brace
   structure casts through the coarse `parse_ir`; true grammar-keyword
   emission is follow-up.
