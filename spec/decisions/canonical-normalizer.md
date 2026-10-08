# Canonical normalizer decision (issue #8)

## The conflict

Two incompatible "normalized compare" definitions existed:

| Definition | Behaviour | Fatal flaw |
|---|---|---|
| Lab `normalize.py::normalize_text` (pre-#8) | collapse whitespace only | whitespace-only; keyword-vs-symbol differences NOT laundered but not unified either — `specializes` and `:>` compare different |
| `sysml2py/tests/functions.py::strip_ws` | remove comments, rewrite `specializes`/`subsets`→`:>`, `redefines`→`:>>`, then delete ALL whitespace | deleting whitespace makes `part x` == `partx` and silently launders keyword-vs-symbol differences |

`strip_ws` is lossy in a dangerous way: two models that are NOT equivalent can
compare equal (`part x` vs `partx`; `stop` inside a word is laundered away).
Any pipeline that uses it as its equivalence oracle will pass non-equivalent
output.

## Decision

The canonical normalizer is **token-stream comparison**:

1. **Significant tokens** (words, unrestricted names, strings, symbols,
   braces, semicolons) are compared **in order**.
2. **Whitespace and comments are trivia** and are dropped.  Comments carry no
   SysMLv2 semantics for equivalence.
3. **An explicit, opt-in alias table** resolves the spellings the grammar
   treats as synonyms: `specializes` == `:>`, `subsets` == `:>`,
   `redefines` == `:>>`.  No other rewriting happens.
4. **Token boundaries are preserved**: `part x` and `partx` are different,
   because the token stream keeps token boundaries.

This is the one canonical, non-lossy normalizer.  The old `strip_ws` is kept
only as a **legacy shim** in `sysml2py/tests/functions.py` while the 56
grammar tests migrate to canonical compare; it is not used by the lab.

## Why token-stream, why this alias table

- **Non-lossy:** the token stream is the maximal information you can keep
  while ignoring whitespace/comments.  It never merges adjacent tokens, so
  boundary-sensitive confusion (`partx`) is impossible.
- **The alias table is the *only* place grammar synonymy lives.**  The sysml2py
  grammar rewrites `specializes`/`subsets` to `:>` and `redefines` to `:>>`
  when parsing; the canonical form does the same at compare time, so a model
  written with the keyword and a model written with the symbol compare equal —
  which is the *correct* equivalence for generated-code round-tripping.
- **Opt-in:** callers that need strict spelling equality pass
  `aliases={}`.

## Single implementation

The normalizer is **self-contained** (`src/sysml2py_lab/normalize.py` has no
lab imports) and the codegen pipeline ships it verbatim into the generated
`sysml2py` package (see `emit.py`).  The lab and the generated library use
the **same** canonical normalizer — the acceptance criterion "one
implementation, shipped by codegen" is met by construction.

## Keep

`normalize_text` (whitespace-collapse) is retained for the existing callers
(generated `dump()` and the MVP round-trip test) but is **not** the corpus
comparison oracle.  Corpus comparisons use `canonical_equals`.
