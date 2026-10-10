# Windtrader validation protocol

**Phase-7 · Issue #12** — the independent correctness oracle.

Windtrader (Tock Ratchetpin's `windtrader` → `windtrader-java`) is the
**sole authority on whether emitted SysML v2 text is syntactically valid**.
The lab must never validate its own assumptions; only the external
Java-backed validator's verdict counts. This file pins the interface.

## 1. Design questions → answers

The issue opened with five blocking design questions. Each is answered by
the actual `windtrader` contract (as-built, verified against 0.2.0):

| Question | Decision |
| --- | --- |
| Invocation mode | **Library (CLI-equivalent)**. Adapter calls `windtrader.validate(text, version=...)` — the wrapper runs `windtrader-java` as an external subprocess (`java -jar … check`). No HTTP, no embedded jar. |
| Auth & network | **None needed at call time.** `windtrader` auto-downloads the pinned `windtrader-java` jar on first use and caches it (`WINDTRADER_CACHE_DIR` / per-user cache). CI installs `windtrader` and provides a JVM; the jar is cached. |
| Determinism of verdicts | **Deterministic per pinned version.** Same text + same `version` → same exit code + same diagnostics. Version is pinned (`0.2.0`) and recorded per file in the manifest. |
| Diagnostic format | **`error: line=… offset=… near=…`** on stderr (exit 2). Normalized by trimming/keeping non-empty lines; locations preserved. |
| Offline / hermetic | **Recorded-fixture mode.** `validate_text(..., recorded=fixtures)` replays committed verdicts keyed by `sha256(text + version)` and never touches the jar. Missing fixture on a hermetic run is an *adapter error* (never a silent valid). CI + unit tests use committed `validate-fixtures.json`. |

## 2. The adapter (`src/sysml2py_lab/validate/windtrader.py`)

Thin, duck-typed wrapper. Per-text:

```
Verdict(status, exit_code, version, diagnostics, duration_s, recorded)
```

- `status ∈ {"valid", "invalid", "adapter_error"}`
- `exit_code`: 0 valid, 2 invalid, anything else → `adapter_error`
  (distinguishable from model-invalid per acceptance criterion #4).

`validate_text` distinguishes three outcomes cleanly:

| Outcome | status | When |
| --- | --- | --- |
| Valid SysML | `valid` | exit 0 |
| Invalid SysML | `invalid` | exit 2 |
| Adapter failure | `adapter_error` | no java / import error / exit 3 / timeout / missing fixture |

A caller can always tell "the model is bad" (exit 2) from "the tooling
couldn't run" — the gate fails loudly on `adapter_error` rather than
treating it as valid or invalid.

## 3. Corpus orchestration (`validate/corpus.py`)

`compute_verdicts(corpus_dir, generated_pkg=None)` validates each file:

1. **raw input text** — the corpus file as merged,
2. **round-trip output** (when a generated package is supplied) — the
   generator-emitted sysml2py text (`text → IR → generated classes →
   get_definition() → dump()`), so we validate what the GENERATOR emits,
   not just what was ingested (acceptance criterion #2).

`update_manifest_verdicts` writes the per-file input verdict into
`corpus/manifest.json`:

```json
"windtrader": {"status": "valid", "version": "0.2.0", "exit_code": 0}
```

Previously-unverified files are recorded as their true verdict (some will
be `invalid` — see §5). `"unverified"` is never treated as valid.

## 4. CLI

`sysml2py-lab validate <file|corpus> [--generated PKG] [--record DIR] [--offline DIR]`

- validate a single file, or the whole corpus.
- `--generated` enables round-trip-output validation.
- `--record` writes a fresh `validate-fixtures.json` (live run).
- `--offline` replays committed fixtures (no JVM needed).

Exit:
- **single-file scope** (strict): `0` when valid; `1` on any `invalid` or
  `adapter_error`.
- **corpus scope** (ratchet, §6): `0` carries the tracked baseline (incl.
  its `invalid`s) forward with no regressions; `1` on any `adapter_error`,
  ratchet regression, round-trip regression, generator `emit_error`, or
  unbaselined/new file. Tool failures are never masked.

Each failure axis is reported as its own counter in the summary so the
operator can distinguish "tooling broke" from "model invalid" from "my
generator emitted bad text" (§1 design question: adapter vs invalid is
never conflated).

## 5. Current corpus verdicts (recorded vs windtrader-java 0.2.0)

As first recorded (the honest baseline):

- **29 / 62 files** validate (input AND round-trip).
- **33 / 62 files** the validator rejects. The dominant systematic cause:
  `import PackageX::*;` namespace imports — windtrader-java 0.2.0 rejects
  `near=import` at these lines. A smaller set fails on other syntax
  (e.g. `near=[` in some attribute forms). Round-trip output preserves the
  same invalid constructs (roundtrip_valid == input_valid), so the
  generator does **not** mask or "fix" what the oracle rejects — the cast
  is honest.

These 33 files predate the windtrader gate (manifest said `"unverified"`).
The systematic `import ::*` grammar gap is **Tock Ratchetpin's to fix** in
`windtrader-java`; the lab will record and gate against that fix once it
lands. Until then the gate enforces:

- **no file regresses** valid → invalid (ratchet),
- **no NEW/unverified file may enter** the corpus without a passing verdict,
- **no adapter_error** (the tooling must always run).

## 6. Gate semantics (CI)

`sysml2py-lab validate corpus --offline corpus/validate-fixtures.json` as a
CI gate on corpus + spec changes — a **ratchet** (same philosophy as issue
#11's coverage/goldens ratchet). It FAILS if:

1. any `adapter_error` (tooling broken / missing fixture) — never masked,
2. any file **regressed** valid → invalid/adapter since the committed
   manifest baseline (the ratchet), on **either** the input **or** the
   round-trip axis,
3. any **round-trip regression**: a file whose *input* the oracle accepts
   but whose *generator output* it rejects (AC#2's invariant — this is a
   lab defect today, no baseline needed),
4. any generator **`emit_error`** (round-trip output could not even be
   produced — a generator crash, distinct from a windtrader verdict),
5. any corpus file that is **unbaselined / new** without a passing verdict
   (AC#3 — enforced separately by the `corpus add` gate).

The 33 **baseline** invalid verdicts are tracked, deliberate WIP (see §5),
NOT gate failures: the gate reports them as a note and passes.  When Tock's
windtrader-java gains the `import ::*` grammar (or the lab's corpus files are
corrected), those files flip to valid and the ratchet records the
improvement.  A separate **live** CI job (`validate corpus --generated`)
runs the real validator against round-trip output and fails on any
adapter_error/regression, so the recorded baseline can never drift silently
from what the actual jar says.

The committed `validate-fixtures.json` records **both** the input and the
round-trip digest per file, so `--offline --generated` (used in CI without
a JVM) replays the round-trip verdicts too.  A **fixture-drift** step in CI
re-records on `--generated` and diffs against the committed file: any drift
fails the check, so a recorded verdict can't silently diverge from the real
oracle.

The ratchet means every recorded `invalid` is a tracked, deliberate item,
not a silent hole — the validator-gap fix in windtrader-java flows through
as files flip to `valid`.
