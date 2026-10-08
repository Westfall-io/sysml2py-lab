# Codegen of AST classes — design (issue #9)

Decision record for the deterministic AST-class generator replacing the 266
hand-written `sysml2py/grammar/classes.py` classes.  Companion to
`spec/decisions/canonical-normalizer.md` (issue #8) and the children/modifier
relationship models (issues #5/#6).

## Goal

`sysml2py-lab generate --out <dir>` writes a complete, self-contained
`sysml2py` package whose `grammar/classes.py` equivalent is generated —
uniformly, from metadata — and satisfies the 0.5.3 dict-shape contract so
`formatting.reformat` output and `usage.py`/`definition.py` builder code keep
working.

## Sources of truth (all already committed)

| Source | Used for |
|---|---|
| `spec/language_spec.json` | rule names, per-rule body structure (seq/alt/assign/call), return types, fragment vs rule vs enum vs terminal |
| `spec/relationships/children.json` | per-body membership slots: `kinds` (child node kinds), `chain` (wrapper path), `relationship`, `cardinality`, `wrapper` |
| `spec/relationships/modifiers.json` | per-prefix modifier slots: `name`, `tokens`, `kind` (flag/value), `cardinality`, `mutually_exclusive_with` |
| `corpus/` + `spec/reports/classes_parity.md` | coverage signal for which kinds actually appear; parity target (266 classes) |

## Architecture

```
language_spec.json ──┐
children.json ───────┼─► codegen/model.py ──► Jinja templates ──► generated sysml2py package
modifiers.json ──────┘        │                  ast_classes.py.j2
                               │                  ast_dispatch.py.j2
                               │                  provenance.py.j2
                               │                  (existing pyproject/init/nodes templates)
corpus manifest ──────────────┘ (generator version + provenance stamp)
```

`codegen/model.py` loads the three spec files into frozen dataclasses and
exposes:

- `rule(name)` — rule lookup
- `node_kinds()` — the FULL set of generated classes: every grammar rule
  (rule/fragment/enum/terminal) + composites.  The old classes.py 266 are a
  strict subset (parity test pins this); the generator emits 700+.
- `body_slots(body_name)` — membership slots for a body
- `dispatch_map()` — `{parent_body: {member_kind: child_class}}` derived from
  children.json chains/kinds.  Keys are generated class names.
- `IR_KIND_ALIASES` — coarse IR kind (`package`, `part`, ...) → generated
  class name.  The issue-#7/#8 IR is a brace-block parse with ~26 coarse
  kinds; the generated classes are fine-grained grammar classes.  This alias
  lifts IR trees onto generated nodes (`Node.from_ir`).

## Generated class shape

Every generated class implements the uniform triad:

```python
class PartDefinition:
    def __init__(self, definition=None, *, raw_text=None):
        if isinstance(definition, dict):
            self.definition = definition
        elif definition is not None:      # IR node / other typed input
            ...
        else:
            self.definition = {"name": "PartDefinition"}
        self.raw_text = raw_text

    def dump(self) -> str: ...           # modifiers in spec order, keyword, body
    def get_definition(self) -> dict: ... # reconstruct the dict shape
```

- `__init__(definition=None)` keeps the old dict-shape contract and adds an
  IR mode: a dict with `"kind"` is IR-JSON (children re-dispatched into
  generated nodes); a dict without `"kind"` is textX-mode
  (`valid_definition(definition, <ClassName>)` semantics preserved); `None`
  is empty construction with the class name.
- `dump()` is loss-minimizing: prefers the node's own `raw_text` verbatim
  (newline-terminated for `//` line comments so a comment cannot swallow its
  siblings — the issue-#8 render_ir lesson), then recurses into children.
  textX-mode dicts reconstruct via `_dump_textx` (modifiers, keyword, name,
  body).  A bare kind with no name emits nothing (no malformed SysML).
- `get_definition()` returns the dict-shaped reconstruction (the builder
  direction): for IR-built nodes it emits the original IR coarse kind via
  `ir_kind` — closing the 265-vs-72 gap and preserving round-trip identity.

## Dispatch (replaces elif ladders)

`ast_dispatch.py.j2` emits a membership-dispatch table built from
`children.json`:

```python
MEMBERSHIP_DISPATCH = {
    "PackageBody": {"PartDefinition": "PartDefinition", ...},
    ...
}
CLASS_TO_BODY = {"Package": "PackageBody", "PartUsage": "UsageBody", ...}

def dispatch_member(parent_body: str, member_kind: str):
    """Return the class handling `member_kind` inside `parent_body`, or
    Unsupported if the grammar allows it but no modelled class exists."""
    cls_name = MEMBERSHIP_DISPATCH.get(parent_body, {}).get(member_kind)
    return getattr(ast_classes, cls_name, Unsupported) if cls_name else Unsupported
```

`CLASS_TO_BODY` maps each class to the body it **owns** (derived from the
grammar rule's own `XxxBody` reference, following fragments — `PartUsage`
owns `UsageBody` via `Usage`).  `Node.from_ir` dispatches children under the
parent's owned body, so the table is genuinely exercised by the cast.

- Generated from `children.json` `kinds` sets + `wrapper`/`chain` — no
  hand-written elif ladders.
- `Unsupported` carries `raw_text` (the loss-minimizing IR principle):
  never raise `NotImplementedError`.

## Provenance (determinism)

`provenance.py.j2` emits:

- grammar hash (sha256 of `language_spec.json` bytes)
- spec hash (sha256 of children.json + modifiers.json + overlay)
- corpus manifest hash (sha256 of manifest.json bytes)
- generator version (from the lab package)
- generated-at timestamp (INJECTED, never `Date.now()`-style nondeterminism;
  the default generation path uses a fixed `SOURCE_DATE_EPOCH`-style value so
  two runs are byte-identical; the CLI may pass an explicit timestamp)

Determinism gate: `generate --out` twice → `diff` byte-identical.

## The Casting (issue #9 acceptance)

- Determinism: two consecutive runs byte-identical.
- `ruff` + `black` clean on the generated package (normalize.py excluded from
  the format check — it is a verbatim copy of the lab's single-sourced
  canonical normalizer, issue #8).
- `>= 266` node kinds generated (the generator emits 700+, and the parity
  test pins every hand-written 0.5.3 class name); every one has both `dump()`
  and `get_definition()`.
- Zero `NotImplementedError` in generated output.
- The CAST: corpus → IR → `Node.from_ir` → generated tree → `dump()` →
  canonical-compare against original source.  **62/62 corpus files round-trip
  losslessly.**  (The grammar_test.py suite needs textX, which is not
  installable in this environment; the IR/dump casting is the equivalent
  round-trip gate over the same 62 corpus files.)
- `generate` does NOT overwrite `sysml2py/src/` without an explicit flag.

## Casting found a real bug

The first full-tree dump hit the same class of bug as issue #8's `render_ir`:
a `//` line-comment `raw_text` joined with no trailing newline swallowed every
following sibling on canonical re-tokenization.  Fixed in the generated
`Node.dump()`/`Unsupported.dump()` (newline-terminate `//` comments) — the
casting gate again caught a generator loss before shipping.

## Coordination

Emitted-syntax changes (dump() keyword/order) may affect what Tock's
validators/parsers read — coordinate before merging.  Vesara/Brella's models
round-trip via the generated classes; the grammar_test corpus is the
regression net.
