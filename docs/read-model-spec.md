# read_model: load a .sysml file, edit it, emit lossless SysML

**Status:** implemented (awaiting PR review/merge)
**Owning repo:** `sysml2py-lab` (parser/loader + lossless render). The
distributable `sysml2py` stays builder-only (decision B).

## Goal

A person writes their own script to do read-modify-write on a `.sysml` file:

```python
from sysml2py_lab import read_model
from sysml2py import PackageBuilder, Part, Attribute

model = read_model("file.sysml")        # an editable, LOSSLESS model
pkg = PackageBuilder("NewSubsystem")
pkg.add(Part("Motor").add(Attribute("torque", value=10, unit="N*m")))
model.add(pkg)
open("file2.sysml", "w").write(model.dump())
```

## Fidelity contract (Chris's decision: EDITABLE + LOSSLESS)

- **No-edit:** `read_model(f).dump()` is canonical-equal to `f` for **every**
  corpus file (verified **61/61**, 100%). Nothing is dropped, reordered, or
  invented. (`canonical_equals` = structural round-trip; whitespace/layout
  normalized — byte-identity is explicitly NOT the contract.)
- **Edit:** `model.add(builder)` appends a builder-authored element; the
  builder renders faithfully for authored content, so an injected package
  survives. The original model's statements are preserved (same elements/
  kinds/names/children/order/values/units) — the model round-trips as a
  whole. LAYOUT note: inter-statement whitespace (blank lines, indentation)
  is normalized by `render_ir`, so untouched content is preserved at the
  model level, NOT re-emitted with identical formatting.

## Why this design (the fork, and the measured resolution)

Two candidate designs were measured (all 61 corpus files):

| Path | no-edit round-trip | editable? |
| --- | --- | --- |
| hydrate -> reconstruct each node as a `sysml2py` Builder, `builder.dump()` | **3/61** | yes |
| `Node.from_ir().dump()` (AST, raw-text-preserving) | 62/62 | no |
| **`ReadModel` chosen: wrap the IR tree, dump via `render_ir`** | **61/61** | **yes** |

The builder-reconstruction path lost fidelity because the parser's IR does not
fully model every header (`part redefines eng` keeps `eng` only in raw_text;
identifier quoting like `'Parts Example-1'` is not reconstructed; typed
multiplicities re-order). Re-encoding from IR *fields* therefore drops what
the parser didn't decompose. The IR itself is lossless (`render_ir` = 61/61),
so the honest editable+lossless model wraps the IR and renders it — edits are
spliced in, untouched statement text re-emits as parsed.

`render_ir` concatenates each node's `raw_text` in preorder (source order),
so losslessness is structural. `ReadModel.add()` appends a chunk (the added
builder's `dump()` text, or a raw string) with a `//`-line-comment termination
guard, spliced into the output.

## Files

- `src/sysml2py_lab/reader.py` — `ReadModel` + `read_model()`.
- `src/sysml2py_lab/__init__.py` — exports `read_model`, `ReadModel`.
- `tests/test_read_model.py` — 61 corpus lossless tests + edit + raw-text.

## House rule

Feature branch -> PR -> review (Claude Code) -> merge. Casting-tested: the
61/61 lossless round-trip + edit tests must be green before merge. Cross-repo:
none needed — this is lab-internal; the distributable stays builder-only.

## Not in scope (per decision B)

- No text parser added to the distributable `sysml2py`.
- No `.insert` alias (`.add` covers it). No `a.Package("C")` accessor (use
  `PackageBuilder("C")` + `.add`).
