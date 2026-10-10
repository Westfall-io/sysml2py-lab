# Issue #12 — Windtrader validation gate

**Status: IMPLEMENTED — independent correctness oracle wired into the lab.**

Phase-7. Windtrader (Tock Ratchetpin's `windtrader` Python wrapper → the
`windtrader-java` SysML v2 validator) becomes the **sole authority on whether
emitted SysML v2 text is syntactically valid**.  See `validate/protocol.md`
for the full interface spec.

## Scope / files
- `src/sysml2py_lab/validate/windtrader.py` — thin adapter (invoke, normalize
  verdict, distinguish adapter-error from model-invalid).
- `src/sysml2py_lab/validate/corpus.py` — corpus orchestration (compute
  verdicts, summarize, ratchet, manifest update).
- `src/sysml2py_lab/validate/protocol.md` — design answers to the five
  blocking questions.
- `src/sysml2py_lab/validate/__init__.py`.
- `src/sysml2py_lab/cli.py` — `sysml2py-lab validate <file|corpus>` +
  corpus-add AC#3 gate (`--allow-invalid` escape).
- `corpus/manifest.json` — per-file `windtrader` field now records real
  verdicts (was `"unverified"`).
- `corpus/validate-fixtures.json` — committed recorded verdicts for hermetic
  replay (no JVM).
- `.github/workflows/ci.yml` — hermetic offline gate job + live round-trip
  validation job (Java job).
- `tests/test_windtrader_validate.py` — 23 tests.

## The five blocking design questions → answers
1. **Invocation mode**: library (`windtrader.validate(text, version=...)`),
   the wrapper runs `windtrader-java` as an external subprocess. No HTTP.
2. **Auth / network**: none at call time — wrapper auto-downloads + caches the
   pinned jar (0.2.0). CI provides a JVM and installs `windtrader`.
3. **Determinism**: deterministic per pinned version; version recorded per file.
4. **Diagnostic format**: `error: line=… offset=… near=…` on stderr, exit 2.
5. **Offline**: recorded-fixture mode (`--offline`, keyed by sha256(text+ver));
   missing fixture = adapter error, never a silent valid.

## Casting / oracle integrity (the honest finding)
Running the real validator over the corpus for the first time produced:
- **29 / 62 files valid** (input AND round-trip output),
- **33 / 62 files invalid** — dominated by a systematic `import X::*;`
  namespace-import form windtrader-java 0.2.0 rejects (`near=import`).

Crucially, **round-trip output validates exactly as the input does**: the
generator does NOT mask or "fix" what the oracle rejects — the cast is honest
at both levels.  When Tock's windtrader-java gains `import ::*` support, those
files flip to valid and the ratchet records the improvement.

Because 33 invalid files are a tracked baseline, the GATE is a **ratchet**
(same philosophy as issue #11), not a strict all-invalid-fails barrier:
it fails on **adapter errors** (tooling broke), **valid→invalid regressions**,
and **unverified/new files** (AC#3), while the tracked baseline invalids are
WIP that Tock's validator fix will retire.

## Acceptance criteria — status
- ✔ Every corpus file has a recorded Windtrader verdict (manifest
  `windtrader` = valid/invalid + version, all 62).
- ✔ Generator-emitted text (round-trip output) validated for every file
  (roundtrip_valid computed; equals input_valid = honest cast).
- ✔ A new example cannot enter the corpus without a passing verdict
  (`corpus add` refuses invalid unless `--allow-invalid`).
- ✔ Adapter failures distinguishable from model-invalid
  (`adapter_error` vs `invalid`; gate fails loudly on adapter_error).

## Ratchet semantics (protocol §6)
`sysml2py-lab validate corpus --offline corpus/validate-fixtures.json` as a
CI gate on corpus + spec changes. Fails if: any adapter_error, any
valid→invalid regression (ratchet vs committed manifest baseline), or any
unverified file. Baseline invalids are reported as tracked WIP and pass.

## Review cycle
Pending review (r1) on feature branch `feat/issue12-windtrader-gate`.
