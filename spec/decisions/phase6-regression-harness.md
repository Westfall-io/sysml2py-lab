# Issue #11 — Phase-6 Regression Harness: Replay, Round-trip, Determinism, Goldens, Coverage Ratchet

**Status: IMPLEMENTED — CI + CLI harness enforcing the corpus loop.**

The lab previously had a single MVP round-trip test and **no CI at all**
(no `.github/`). This issue makes the corpus loop enforceable.

## Deliverable map

- **`src/sysml2py_lab/regress.py`** — the harness. Composes the existing
  corpus / IR / codegen pieces into reusable, CI-runnable functions:
  - `replay()` — every corpus file → text→IR→render (canonical) AND
    text→IR→generated classes→dump (canonical), with a per-file fidelity
    class (`full`/`partial`/`opaque` = all/partly/none modelled).
  - `roundtrip()` — text → generated classes → `get_definition()` → `dump()`
    → canonical. This is the direction the issue called out as "currently
    blocked by the 72/266 get_definition gap" — with phase-5 (issue #10)
    landed, the **whole corpus now round-trips 62/62**.
  - `determinism()` — generate twice, require byte-identical.
  - `goldens()` — committed `goldens/replay-fidelity.json` +
    `coverage-baseline.json`; `write=False` diffs for drift, `write=True`
    refreshes intentionally.
  - `coverage()` — modelled/buildable/round-tripping counts vs a committed
    baseline, with a **ratchet that FAILS on regression** (mutation-proven:
    a baseline claiming more coverage than reality returns `pass=False`).
- **CLI `sysml2py-lab regress {replay,roundtrip,determinism,goldens,coverage}`**
  — the same loops from the command line (exit 0/1 on pass/fail), so CI can
  run them directly. `--generated <pkg>` enables the AST/roundtrip stages.
- **`.github/workflows/ci.yml`** — runs on every push/PR to main: install lab,
  ruff, the full pytest suite, generate + determinism, regress
  replay/roundtrip/goldens/coverage-ratchet, corpus verify.
- **`dump_block_mvp` migrated** out of `tests/test_mvp_roundtrip.py` into
  `sysml2py_lab/model.py` (acceptance criterion).

## Casting status

**The corpus casts clean at both levels.** `replay` reports all 62 files
passing the IR canonical re-render AND the generated-classes dump canonical
compare; `roundtrip` (the harder, semantic, direction) also passes 62/62 with
phase-5's `get_definition()` → `dump()` in place. Fidelity: 21 files full /
41 partial / 0 opaque; fidelity totals 1255 modelled / 146 partial / 387
opaque (unchanged baseline).

## Coverage baseline (committed goldens)

```
modelled=1255  partial=146  opaque=387
buildable=21   ir_roundtrip_files=62   files=62
```

The ratchet is monotone non-decreasing: CI fails if any of these fall.

## Notes / scope

- **No CI configured on the repo yet** — the workflow is committed but the
  repo has zero prior check-runs, so the first Green CI will appear once this
  PR merges to main (GitHub Actions only runs on the default branch / PRs
  against it). Verified locally via the CLI loops and pytest.
- `buildable` counts corpus node-kinds that map to a generated builder class
  (via the generated `IR_KIND_ALIASES` surface) — requires `--generated` so
  the generated package is importable.
- Failure fidelity: replay/roundtrip produce per-file pass/fail lists; a new
  corpus file the spec cannot model surfaces as a named failing entry rather
  than a silent `Line` fallback (the per-file `ast_roundtrip`/`ir_roundtrip`
  flags, plus the coverage ratchet, make silent degradation visible).
- Determinism is asserted twice (harness + the pre-existing
  `test_determinism_byte_identical`) and via CI `diff -r`.

## Follow-up (out of scope)

- Issue #12 (Windtrader gate) will add the independent correctness oracle
  (validating the generator-emitted text, not just the input).
- Issue #13 (packaging) will consume the determinism + regression harness to
  gate the release pipeline.
