# Issue #10 — Phase-5 Developer API: Dependency Posture & Casting Status

Status: foundation through r4 (r1→r4: every REQUEST CHANGES cycle fixed,
r4 fixes under re-review).  Casting clean at semantic
(build → Node → get_definition) and coarse-IR syntactic-parse-back levels;
multi-member brace structure recovered via `;` (r3 W1-8).  Full 0.5.3
compat contract is a follow-up (see Deliberate Breaks).

## Dependency posture (DECIDED: self-contained-thin)

The hand-written 0.5.3 surface is 1,210 lines backed by textX grammar and
astropy Quantity.  Per the epic's "self-contained" mandate and this
environment (no instorable textX/astropy), issue #10 adopts **self-contained
thin**: the generated builder/traversal/printer target the canonical IR /
generated AST directly, with **no runtime textX or astropy dependency**.

## Generated surface (new, all derived from spec + generated AST)

- `builders.py` — `Builder` base + per-kind builder classes derived from
  `IR_KIND_ALIASES` minus a deny-list (`BUILDER_DENY`), so coverage cannot
  drift from the alias table (W2-18).  23: Package, PartUsage,
  ItemUsage, AttributeUsage, PortUsage, ConstraintUsage, ConnectionUsage,
  OccurrenceUsage, ActorUsage, ActionUsage, StateUsage, SubjectUsage,
  UseCaseUsage, RequirementUsage, InterfaceUsage, Message,
  ObjectiveRequirementUsage, Succession, TransitionUsage, Comment,
  AliasMember, Import, ReferenceUsage
  (`_DefaultReferenceBuilder` is the internal directed-feature default,
  not in `__all__`).  Methods:
  `_set_name` / `_set_short_name` / `_set_text` / `_set_multiplicity` /
  `_set_typed_by` / `_set_child` / `_get_child` / `add_directed_feature` /
  per-modifier setters (from modifiers.json BasicUsagePrefix) / `build` /
  `build_node` / `dump` / `_render_sysml` / `is_legal_child`.
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

Self-verification fixes (pre-r3 batch): enum modifier setters key by slot
NAME so `_set_direction("out")` is `{'direction':'out'}`; regression guard
test added.

### r3 (REQUEST CHANGES) — 2 blockers + 8 W1 + 6 W2, all fixed (this commit)
- **B1** `_DefaultReferenceBuilder.ir_kind="reference"` was NOT in
  `IR_KIND_ALIASES`, so every `add_directed_feature()` product silently lifted
  to `Unsupported`.  Added `"reference": "ReferenceUsage"`; directed features
  now build to a real `ReferenceUsage` node.  Test now asserts the NODE TYPE.
- **B2** the direction enum (`in`/`out`/`inout`) was absent from
  `_parse_header`'s modifier whitelist, so `out part P` reparsed as
  `unknown`/opaque.  Added `in`/`out`/`inout` (+ `individual`/`snapshot`/
  `timeslice`) to the `ir.py` modifier tuple.  (Tock coordination: shared
  `ir.py` — flagged for his review.)
- **W1-1→3** `owned_body_for` recursed from `DefinitionBody` and wandered to
  `PackageBody`; now returns the delegated `*Body` directly → usage builders
  own `DefinitionBody` (the populated membership body).
- **W1-4** `is_legal_child` is now a REAL gate (not always-True): checks
  `child.kind` against `LEGAL_CHILDREN[owned_body]`; e.g. rejects ActorUsage /
  ObjectiveRequirementUsage under a part.  New test.
- **W1-5/6** short name emitted as angle `<s>` in BOTH `_render_sysml` and
  `Node.dump()` (was `~s` in one, dropped in the other).  New test.
- **W1-7** `Comment._set_text` emits `/* text */` (self-terminating) so it
  reparses as a `comment` node.  New test.
- **W1-8** `_render_sysml` now emits `;` on childless statements → multi-member
  brace blocks recover as SEPARATE IR statements (was one swallowed node).
  New test; gap #5 rescoped.
- **W1-9** field-level text round-trip tests added (kind/name/modifiers/
  type_refs/multiplicity recovered per element from `parse_ir(builder.dump())`).
- **W2-10** modifiers emitted in canonical slot order (was insertion order).
- **W2-11** mutually-exclusive flag modifiers clear each other (abstract vs
  variation).  New test.
- **W2-12** enum setters validate against allowed tokens.  New test.
- **W2-14** `_set_multiplicity` API added; emitted as `[n]`, preserved in IR.
- **W2-15** `_walk_chain` matches LONG name then short name, so dotted chains
  resolve even when members carry short names.

Gate: 166 tests pass; source + generated ruff-clean; determinism
byte-identical; PR #19.

### r4 (REQUEST CHANGES) — 1 blocker + 3 W1 + 5 W2, fixed (this commit)
- **#1 [BLOCKER]** `is_legal_child` false-rejected every usage under a
  Package (PackageBody listed only definitions, but a package legitimately
  contains `part`/`attribute` members — e.g. `examples/family.sysml`).
  Fixed: union usage kinds (`DefinitionBody`) into `PackageBody`'s allowed
  set; added a Package-parent test case.
- **#2 [W1]** slot-order emission never fired for flags (lookup-by-name vs
  store-by-token mismatch).  Fixed both `_render_sysml` and `_to_ir` loops to
  look up flags by token, enums by name; widened the fallback skip.  Now
  `out end ref` (canonical), not insertion order.  Test added.
- **#3 [W1]** `_set_text` early-return erased the whole subtree for
  non-Comment builders.  Scoped the `/* */` early-return to `comment` only;
  other kinds append ` /* text */` after the header.  Verified subtree + text
  both emit.
- **#4 [W1]** B2/ir.py direction change had no regression test.  Added
  `parse_ir("out part P;")` assertions.
- **#5 [W2]** W1-9's claim exceeded its test (type_refs/multiplicity never
  asserted; `_set_multiplicity` untested).  Added field-level assertions.
- **#6 [W2]** W2-15 applied to builder chain but not `resolve_chain`.
  Fixed traversal's chain to match short_name too.
- **#7 [W2]** `_walk_chain` root self-name preferse short name.  Now matches
  LONG or short name.
- **#8 [W2]** `Node.dump()` reconstruct concatenated siblings (builder-built
  nodes, no raw_text).  Now joins with `\n` when raw_text is empty.
- **#9 [W2]** decision-doc count/list/methods/status refreshed (23 concrete
  builders incl. Import + ReferenceUsage).

Note: ir.py's 9 pre-existing lint findings remain untouched (Tock domain);
the `individual` token added in r3 B2 is inert (already a kind stem) —
harmless, kept for symmetry.

Gate: 167 tests pass; source + generated ruff-clean; determinism
byte-identical; PR #19.

### r5 (REQUEST CHANGES) — 1 W1 + 5 W2, fixed (this commit)
- **W1-1** `is_legal_child` dropped the conservative-True fallback: a body
  with no children.json key (e.g. Import→RelationshipBody) rejected every
  child.  Restored: bodies not in LEGAL_CHILDREN never reject.  Test added.
- **W2-2** non-comment `_set_text` had no test → added
  `test_builder_noncomment_set_text_keeps_subtree` (header + subtree + text).
- **W2-3** non-comment `_set_text` landed in `raw_text`, so `Node.dump()`
  dropped the header and glued the subtree.  Text now persisted only for
  Comment; other kinds render text via `_render_sysml` only.
- **W2-4** `resolve_chain` root segment matched name-only (vs `_walk_chain`
  long-or-short) → now matches long OR short name.
- **W2-5** decision doc now discloses the `Node.dump()` path losses
  (no braces/`;` → subtree collapse on re-parse; comment name dropped;
  comment text undelimited) as gap #7.
- **W2-6** fallback modifier-skip matched only `s[1][0]`; now `k in s[1]`
  (robust to multi-token flag slots on spec refresh).

Gate: 168 tests pass; source + generated ruff-clean; determinism
byte-identical; PR #19.

## Known gaps / deliberate breaks vs 0.5.3 (documented, follow-up)

1. **Child-legality derivation**: now a REAL gate via `DefinitionBody`/body
   slots; correctness depends on children.json already being accurate for the
   resolved bodies.  Not yet a textX-grammar containment contract (follow-up).
2. **Value API (astropy Quantity)**: not yet generated; the 0.5.3 21-level
   expression tower + `Attribute.set_value` Quantity path are not reproduced.
3. **textX `load`/`load_from_grammar`**: not reproduced (no runtime textX).
4. **class_test.py 53-test parity**: not runnable in this environment (no
   textX/astropy); textX-gated / pending dependency posture.
5. **`_render_sysml` emits coarse IR vocabulary, not full grammatical SysML**:
   kinds are coarse IR words (`part`, `attribute`) and `use_case` renders as
   `use_case` (not `use case`), enum values emit bare — so it is comparable
   with `parse_ir` but not keyword-perfect SysML.  `;` terminators (r3 W1-8)
   mean multi-member brace blocks DO recover structurally now.  True
   grammar-keyword emission is follow-up.
6. **`_get_grammar()` compat break**: returns `build_node()` (a generated
   Node), whereas 0.5.3 returned a textX model object.  Deliberate under the
   self-contained posture.
7. **`Node.dump()` path losses (r5 W2-5)**: for BUILDER-BUILT trees (no
   `raw_text`) the reconstruct branch emits headers/`<s>`/`: ref` but **no
   braces or `;`**, so re-parsing collapses the whole subtree into one
   statement (the r4 #8 newline only prevents word-gluing — it does not imply
   structural recovery); a `CommentBuilder("C")._set_text(...)` drops the
   name `C` in `dump()`; and a comment's text dumps undelimited on the Node
   path, re-parsing as `unknown`/opaque.  The BUILDER path
   (`_render_sysml`) is the canonical emitter and casts cleanly; the Node
   reconstruct path is for parser-produced trees.
