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

## Review-cycle facts & fixes

### r1 (REQUEST CHANGES) — 3 blockers + 7 W1 + 10 W2, all fixed (`6132b41`)
- **B1** `_set_typed_by` wrote a non-existent `typed_by` IR key (throw); now
  emits `type_refs`.  Dropped dead `definition` key.
- **B2** child-legality gate promoted a non-load-bearing resolver to a hard
  raise that rejected legal models; made ADVISORY until the per-kind
  containment resolver is pinned.
- **B3** `_is_definition` set in `__init__` shadowed the generated class flag;
  now a class attribute (default False, package True).  `:>` uses `typed_by`.
- **W1-4/5** usage modifiers on all builders; value/enum modifiers keep value.
- **W1-6** `add_directed_feature` kind_name lookup keyed by node-kind name.
- **W1-7** printer routes builder trees through `_render_sysml`.
- **W1-8/9/10** cast tests hardened + behavioural coverage added.
- **W2** Visitor post-order, `_walk_chain` self-name, dead `_set_child`
  override removed, printer shadow, UPSERT_CHILD_KINDS cleanup, coverage
  pinned to IR_KIND_ALIASES.

### r2 (REQUEST CHANGES) — 5 W1 + 6 W2, all fixed (this commit)
- **W1-1** `_owned_body_for` was a real bug: it recursed into the optional
  `ValuePart` (ExpressionBody) before the base `UsageBody`.  Fixed to scan all
  `*Body` calls in a first pass.  Now `PartUsage` resolves correctly
  (PackageBody) and the strict child gate is *viable* — but kept ADVISORY
  because `UsageBody` has no slots in children.json yet.
- **W1-3** modifier/kind vocabulary now matches the canonical IR: builders
  emit the COARSE IR kind (`part`, not `PartUsage`) and GRAMMAR modifier
  tokens (`abstract`, `end`, `ref`), so `build()` is genuinely comparable with
  `parse_ir` output.  Enums emit the bare value, not `key=value`.
- **W1-4** `Node.dump()` reconstruct branch now preserves `short_name`
  (`~name`) and `type_refs` (`: ref`) so `print_model(build_node())` no longer
  drops authored content.
- **W1-5** `UPSERT_CHILD_KINDS` (unimplemented semantics) dropped from
  `__all__`; added `Builder.is_legal_child()` consulting `LEGAL_CHILDREN`
  (advisory where populated).
- **W2-1** `BUILDER_DENY` now filters the explicit `order` list too (was only
  the extra tail) — single source of truth.
- **W2-2** coverage test asserts SET EQUALITY (both directions).
- **W2-3** syntactic cast asserts recovered shape from the brace-OwNER, not
  the root.
- **W2-4** dead generation data (`definition_modifier_slots`, `named_kinds`,
  per-class `modifier_slots`) removed from model/emit.
- **W2-5** `_set_typed_by`/`_render_sysml` use `:` (typing) not `:>`; referent
  `_name`.
- **W2-6** dead `hasattr(feat,"_set_direction")` guard + duplicate
  `_DefaultReferenceBuilder._set_direction` override removed (direction setter
  is the generated enum setter).
- **W2-8** `import` un-denied (`ImportBuilder`); `CommentBuilder._set_text`
  lands in `raw_text`.

Self-verification fixes (same batch, pre-r3): enum modifier setters key by
slot NAME (not `tokens[0]`) so `_set_direction("out")` is `{'direction':'out'}`
(emits bare `out`); `is_legal_child` returns True (never rejects — usage
membership body not load-bearing in children.json yet; a rejecting gate would
reject legal models); regression guard test added.

Gate: 162 tests pass; source + generated ruff-clean; determinism
byte-identical; PR #19.

## Known gaps / deliberate breaks vs 0.5.3 (documented, follow-up)

1. **Child-legality derivation**: ADVISORY only — `owned_body_for()` resolves
   usage kinds broadly; `UsageBody` is not a children.json body key.  A strict
   per-kind containment gate is a follow-up.
2. **Value API (astropy Quantity)**: not yet generated; the 0.5.3 21-level
   expression tower + `Attribute.set_value` Quantity path are not reproduced.
3. **textX `load`/`load_from_grammar`**: not reproduced (no runtime textX).
4. **class_test.py 53-test parity**: not runnable in this environment (no
   textX/astropy); textX-gated / pending dependency posture.
5. **`_render_sysml` is not fully grammatical SysML, and the coarse
   `parse_ir` does NOT recover multi-member brace blocks structurally**: a
   single-child model (Sat{Panel}) recovers through `parse_ir`, but a
   multi-member block (Sat{Panel, Mass}) is flattened by the coarse brace
   parser — the members become siblings whose raw_text swallows the rest.
   The syntactic dump is grammatical, human-readable SysML, and the
   SEMANTIC cast (build -> Node -> get_definition) is element-for-element;
   full grammar-keyword emission + structural multi-member parse-back is
   follow-up.  The cast tests correctly assert only single-child recovery.
