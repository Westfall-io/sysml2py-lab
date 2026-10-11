# Phase-8 — Package, provenance, release the generated library (issue #13)

## Decision: name/version

Chris (product owner) decided the generated distribution ships under the
existing name **`sysml2py`** at version **1.0.0** — the next major line that
supersedes the hand-maintained 0.5.x textX/astropy parser (rather than a
distinct `-gen`/`-core` name, and rather than a 0.0.0 stub).

## What changed (PR #24)

Generated package (`templates/` + `codegen/emit.py`):
- `EmitOptions.version` and CLI `generate --version` default **0.0.0 → 1.0.0**.
  Flows into `pyproject.toml` (name/version), `__init__.__version__`, and
  `provenance.generator_version` (single source of truth).
- `__init__.py` now exposes `__version__` and carries the do-not-edit header
  (all other emitted `.py` already did).
- Compiled grammar (`spec/tx/*.tx`) now ships as package data under
  `src/sysml2py/grammar/` (issue #13 AC2 "the wheel contains the grammar
  resource"); the pyproject uses the canonical hatchling src-layout pattern —
  `[tool.hatch.build.targets.sdist]` keeps the `src/` prefix in the sdist,
  `[tool.hatch.build.targets.wheel] packages = ["src/sysml2py"]` selects all
  package files (W2-3: a bare top-level `include` on `[tool.hatch.build]`
  silently empties the wheel from `python -m build`).
- README states version + migration pointer; runtime stays pure-stdlib.

Acceptance tests (tests/test_ast_codegen.py, issue #13 section):
- default version is 1.0.0 (init/pyproject/provenance).
- every emitted `.py` carries the do-not-edit header.
- wheel ships >=3 grammar `.tx` and pyproject declares them.
- import-graph: clean-venv import pulls no `textx`/`astropy`/`yaml`/`sysml2py_lab`
  (AC3, subprocess with clean `sys.modules`).

Release machinery:
- `.github/workflows/release.yml` — **two ways to release**:
  1. **Tag push** `v1.0.0`/`v1.1.0` → regenerate deterministically with the
     tag-derived version (W2-1: a `v1.0.1` builds 1.0.1, not a re-publish)
     → unit+corpus+cast suite → regress replay/roundtrip/goldens/coverage →
     corpus verify → **live** windtrader round-trip gate (W2-4: publish is
     irreversible, use the strongest oracle) → build wheel+sdist → assert
     wheel contents (grammar/provenance/exact version) → clean-venv
     import-graph smoke → upload artifacts → **publish** (trusted
     publishing, `environment: pypi`).
  2. **Manual `workflow_dispatch`** (owner-driven release from the Actions
     UI): same build + verify, and optionally publishes when
     `publish_to_pypi: true` (default `false` → artifact-only dry-run).
     The `version` input overrides the built version for a manual run
     (default 1.0.0); tag push always derives from the tag. The publish
     gate is `tag || (workflow_dispatch && publish_to_pypi)`.
- `docs/migration-0.5-to-1.0.md` — migration note for 0.5.x consumers.

## Reproducibility

A release is reproducible from `(lab commit, spec, corpus manifest)` alone:
`generate` is deterministic (fixed `generated_at`), and `provenance.py` hashes
the committed grammar/spec/children/modifiers/corpus manifests + generator
version, so the installed wheel self-identifies its exact inputs.

## Publishing note (owner action)

`release.yml` `publish` job uses PyPI **trusted publishing**
(`id-token: write`, `environment: pypi`). Chris (owner) must configure the
repo `pypi` environment + a PyPI trusted-publisher mapping to the
`sysml2py` project before the first tagged publish. Until then, tagged runs
build + verify + upload artifacts but do not publish.
