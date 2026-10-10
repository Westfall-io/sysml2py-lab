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
- `tests/test_windtrader_validate.py` — 31 tests.

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
valid→invalid regression (ratchet vs committed manifest baseline), any
**round-trip regression** (input valid but generator output invalid — AC#2),
any generator **emit_error** (round-trip crash), or any **unbaselined**
new file without a passing verdict. Baseline invalids are reported as
tracked WIP and pass. `--offline --generated` replays round-trip verdicts
(committed fixtures carry input + round-trip digests).

## Review cycle (r1 → r2 → r3)
- **r1: REQUEST CHANGES** — 6 genuine blockers (B1 round-trip gate can never
  fail; B2 `unverified` was dead/AC#3 unenforced; B3 fixture trust anchor
  unverified; B4 zero CLI gate tests; B5 generator crash misclassified as
  adapter_error; B6 `--offline + --generated` structurally broken) + 12 W1
  (hoist quadratic round-trip pass; split summarize buckets by input/roundtrip
  axis; thread verdict+fixture in `corpus add`; fixture provenance; emit_error
  bucket; `record_verdicts` dead) + W2.
- **r2: REQUEST CHANGES — 1 blocker + 5 W1 + 7 W2**.
  - **Blocker (real)**: single-file `validate` summary lacked
    `roundtrip_adapter_errors` → shared gate KeyError'd → a VALID single file
    exited 1. My earlier smoke tests predated the r1 summary restructure, and
    B4's CLI tests only covered directory scope — caught by the reviewer.
  - **W1-1/2/3/4/5**: single shared fixture builder (`build_fixture_dict` /
    `write_fixture_file` in corpus.py — CLI `--record` + CI drift now use it,
    no more three divergent builders); gate BEFORE on-disk writes (a failing
    run no longer clobbers the baseline/trust anchor); round-trip diagnostics
    preserved and shown on round-trip failures; B6 (`--generated --offline`)
    now has a CLI gate test; `corpus add` no longer writes a half fixture
    (stamps manifest with actual verdict values; fixture re-record is a
    deliberate `validate --record` step).
  - **W2**: `import sys` (no `__import__("sys")` hack); `_drop_pkg` in
    finally + `_RT_CACHE` to avoid the double full-corpus generator pass;
    `pytest.raises` instead of assert-False; single-file `--generated` now
    warns it's ignored; on-disk-but-not-in-manifest files warned (invisible
    to the gate otherwise).
- **r3: PENDING** — gate after fixes: 211 tests, ruff clean, single-file
  valid→0 / invalid→1 (blocker smoke), offline corpus ratchet exit 0,
  shared builder reproduces committed fixtures 62/62.
