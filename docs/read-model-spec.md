# read_model: load a .sysml file, edit it, emit lossless SysML

**Status:** implemented. Lab round-trip landed via PR #34; the read-side
modules were then ported into the user-facing distributable (generated
`sysml2py` gains `read_model`/`ReadModel`) — see "Port into the distributable"
below. The distributable is **no longer builder-only by dint of this feature**:
read/modify/emit is part of the user-facing baseline, since `sysml2py` (not the
lab factory) is what model authors install.

## Goal

A person writes their own script to do read-modify-write on a `.sysml` file:

```python
from sysml2py import read_model          # the user-facing import (also works
from sysml2py import PackageBuilder, Part, Attribute   # from sysml2py_lab)

model = read_model("file.sysml")        # an editable, LOSSLESS model
pkg = PackageBuilder("NewSubsystem")
pkg.add(Part("Motor").add(Attribute("torque", value=10, unit="N*m")))
model.add(pkg)
open("file2.sysml", "w").write(model.dump())
```

`from sysml2py import read_model` is the supported entry point; `sysml2py_lab`
re-exports the same implementation for the factory/test path.

## Fidelity contract (Chris's decision: EDITABLE + LOSSLESS)

- **No-edit:** `read_model(f).dump()` is canonical-equal to `f` for **every**
  corpus file (verified **62/62**, 100%). Nothing is dropped, reordered, or
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

Three candidate designs were measured (whole-corpus denominator: 62 files):

| Path | no-edit round-trip | editable? |
| --- | --- | --- |
| hydrate -> reconstruct each node as a `sysml2py` Builder, `builder.dump()` | **3/62** | yes |
| `Node.from_ir().dump()` (AST, raw-text-preserving) | 62/62 | no |
| **`ReadModel` chosen: wrap the IR tree, dump via `render_ir`** | **62/62** | **yes** |

The builder-reconstruction path lost fidelity because the parser's IR does not
fully model every header (`part redefines eng` keeps `eng` only in raw_text;
identifier quoting like `'Parts Example-1'` is not reconstructed; typed
multiplicities re-order). Re-encoding from IR *fields* therefore drops what
the parser didn't decompose. The IR itself is lossless (`render_ir` = 62/62),
so the honest editable+lossless model wraps the IR and renders it — edits are
spliced in, untouched statement text re-emits as parsed.

`render_ir` concatenates each node's `raw_text` in preorder (source order),
so losslessness is structural. `ReadModel.add()` appends a chunk (the added
builder's `dump()` text, or a raw string) with a `//`-line-comment termination
guard, spliced into the output.

## Port into the distributable (this feature is in the user-facing baseline)

`sysml2py-lab` is the factory; `sysml2py` is what model authors install. A
read/modify capability that only worked by importing from the factory would be
a leaky abstraction, so the read side was ported into the generated package:

- `emit_sysml2py` now copies `lexer.py` + `ir.py` + `reader.py` verbatim into
  the generated `sysml2py` package (the same single-sourcing mechanism already
  used for `normalize.py`).
- The generated `sysml2py/__init__.py` re-exports `read_model`/`ReadModel`.
- The whole read side is **pure stdlib** (~870 lines: lexer 170, ir 555,
  reader 143) and talks only via intra-package relative imports, so it is
  self-contained inside the distributable and adds no runtime dependencies.
- The copied modules are clean under ruff **defaults** (the generated package's
  own `pyproject.toml` pins no `select`, so default rules apply). That forced
  clearing a small amount of pre-existing lab debt in `ir.py` (unused top-level
  `Path` import, `typing.Iterator` -> `collections.abc`, blind `except
  Exception` narrowed, quoted forward-ref annotations unquoted) and matching
  `lexer.py`'s import layout to `normalize.py`'s docstring-first pattern. The
  lab's conservative `[tool.ruff.lint] select` + the ir.py E402 per-file-ignore
  remain for the factory-side files.

## Files

- `src/sysml2py_lab/reader.py` — `ReadModel` + `read_model()` (single source).
- `src/sysml2py_lab/codegen/emit.py` — copies lexer/ir/reader into the
  generated package.
- `src/sysml2py_lab/codegen/templates/pkg_init.py.j2` — re-exports
  `read_model`/`ReadModel` from the generated `sysml2py.__init__`.
- `tests/test_read_model.py` — 62 corpus lossless tests + edit + raw-text +
  comment guard + **generated-package export + generated round-trip/edit**
  tests (build the package in-test via `emit_sysml2py`).

## House rule

Feature branch -> PR -> review (Claude Code) -> merge. Casting-tested: the
62/62 lossless round-trip + edit tests must be green before merge, including
`test_generated_code_passes_ruff` (the ported modules must stay ruff-clean
under the generated package's default select).

## Not in scope

- No `.insert` alias (`.add` covers it). No `a.Package("C")` accessor (use
  `PackageBuilder("C")` + `.add`).
