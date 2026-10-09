# Issue #11 — Phase-6 Regression Harness: Replay, Round-trip, Determinism, Goldens, Coverage Ratchet

**Status: IMPLEMENTED — CI + CLI harness enforcing the corpus loop.**

The lab previously had a single MVP round-trip test and **no CI at all**
(no `.github/`). This issue makes the corpus loop enforceable.

## Review cycle (r1 → fix)

r1: **REQUEST CHANGES** — 3 blockers + 5 W1 + 12 W2 (all genuine). Fixed and
re-submitted (r2 pending).  The r1 findings sharpened the harness from
"composes the pieces" to "a CI gate that actually fails where it must":

- **B1** replay `ok` now requires the AST stage (`ast_roundtrip is True`) when
  a generated package is supplied — a corpus can no longer green on IR alone
  while a broken/absent generated package silently no-ops.  Failing entries
  print their collected `errors`.
- **B2** the coverage ratchet split directions: `modelled`/`buildable`/
  `ir_roundtrip_files` must not decrease; `partial`/`opaque` (loss counts)
  must not INCREASE.  Mutation-proven in both directions.
- **B3** the CI lint step was red-on-first-run because the repo has ~100
  pre-existing ruff-0.16 findings in unchanged files (ir.py, grammar/, model.py,
  cli.py BLE001s) owned by other maintainers — it was never ruff-clean under
  0.16 defaults.  **Scoped the CI lint to this PR's surface + the generated
  package** (harness + generated output must stay ruff-clean) rather than
  churning Tock's pre-existing debt in a regression-harness PR.  The two NEW
  findings my code would have introduced (ir.py unused `Iterable`, cli.py
  unused `render_ir`/subparser bindings) were removed so the scoped gate is
  green.
- **W1-1** `--generated` accepts both `<out>` and `<out>/sysml2py` layouts and
  raises (hard-fails) when neither resolves — no silent AST-stage skip.
- **W1-2** an absent committed golden is drift, not CLEAN; no `mkdir` in check
  mode.
- **W1-3** missing/empty corpus manifest raises (no vacuous green on an empty
  corpus).
- **W1-4** roundtrip now also asserts `get_definition() == ir_to_json(root)`
  (`semantic_ok`), so a `get_definition()` that drops fields can't pass on the
  raw_text that `dump()` returns verbatim.
- **W1-5** determinism always appends its own `--out` after caller args and
  fails if it compared zero files (no vacuous pass).
- W2: dropped dead `_sha256`; fidelity-failure now labelled `"error"` (not a
  fabricated `"partial"`); `--goldens-dir` fully honoured (baseline ratchet
  reads the goldens-dir copy); replay golden snapshots `ast_roundtrip`;
  `determinism` cleans up its temp workspace; `_drop_pkg` purges the cached
  `sysml2py` module; `--corpus/--model/--generated` moved onto each subparser
  (subcommand-first form); `_count_buildable` honours `BUILDER_DENY`.

## Deliverable map

- **`src/sysml2py_lab/regress.py`** — the harness. Composes the existing
  corpus / IR / codegen pieces into reusable, CI-runnable functions:
  - `replay()` — every corpus file → text→IR→render (canonical) AND
    text→IR→generated classes→dump (canonical), with a per-file fidelity
    class (`full`/`partial`/`opaque`/`error`).
  - `roundtrip()` — text → generated classes → `get_definition()` → `dump()`
    → canonical, PLUS the lossless-definition check.  This is the direction
    the issue called out as "currently blocked by the 72/266 get_definition
    gap" — with phase-5 (issue #10) landed, the **whole corpus now round-trips
    62/62** at both the raw and semantic level.
  - `determinism()` — generate twice, require byte-identical; fails on
    zero-files-compared.
  - `goldens()` — committed `goldens/replay-fidelity.json` +
    `coverage-baseline.json`; `write=False` diffs for drift (an absent golden
    is drift), `write=True` refreshes intentionally.
  - `coverage()` — modelled/buildable/round-tripping counts vs a committed
    baseline, with a **ratchet that FAILS on regression** (mutation-proven,
    split directions).
- **CLI `sysml2py-lab regress {replay,roundtrip,determinism,goldens,coverage}`**
  — the same loops from the command line (exit 0/1 on pass/fail).
  `--generated <pkg>` enables the AST/roundtrip stages (accepts both the
  `generate`-printed `<out>/sysml2py` and the `<out>` root).
- **`.github/workflows/ci.yml`** — runs on every push/PR to main: install lab,
  scoped ruff + generated-output ruff, the full pytest suite, generate +
  determinism, regress replay/roundtrip/goldens/coverage-ratchet, corpus
  verify.
- **`dump_block_mvp` migrated** out of `tests/test_mvp_roundtrip.py` into
  `sysml2py_lab/model.py` (acceptance criterion).

## Casting status

**The corpus casts clean at both levels.** `replay` reports all 62 files
passing the IR canonical re-render AND the generated-classes dump canonical
compare; `roundtrip` reports 62/62 for both the raw dump and the lossless
`get_definition() == IR` semantic check.  Fidelity: 21 files full / 41
partial / 0 opaque / 0 error; fidelity totals 1255 modelled / 146 partial /
387 opaque (unchanged baseline).

## Coverage baseline (committed goldens)

```
modelled=1255  partial=146  opaque=387
buildable=21   ir_roundtrip_files=62   files=62
```

The ratchet is monotone in the correct directions: CI fails if progress
counts fall or loss counts rise.

## Notes / scope

- **No CI configured on the repo yet** — the workflow is committed but the
  repo has zero prior check-runs, so the first Green CI will appear once this
  PR merges to main (GitHub Actions only runs on the default branch / PRs
  against it). Verified locally via the CLI loops and pytest.
- `buildable` counts corpus node-kinds that map to a generated builder class
  (via the generated `IR_KIND_ALIASES` surface, excluding `BUILDER_DENY`) —
  requires `--generated` so the generated package is importable.
- Failure fidelity: replay/roundtrip produce per-file pass/fail lists; a new
  corpus file the spec cannot model surfaces as a named failing entry with
  its `errors`, plus a coverage-ratchet or AST-gate trip, rather than a silent
  `Line` fallback.
- Determinism is asserted twice (harness + the pre-existing
  `test_determinism_byte_identical`) and via CI `diff -r`.
- **ci.yml lint scope**: only `regress.py` + `test_regress.py` + generated
  output are lint-gated (see r1 B3 above).  A future issue can drop the
  pre-existing whole-repo ruff debt and widen the gate.
