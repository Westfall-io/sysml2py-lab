"""Corpus-level validation orchestration (issue #12).

Composes the per-text windtrader adapter with the corpus manifest:
* validate every corpus file's RAW text (the input that must be valid),
* when a generated package is supplied, ALSO validate the round-trip output
  (the generator-emitted sysml2py text — AC: round-trip output is validated,
  not just the input),
* record per-file verdicts into the manifest ``windtrader`` field
  (input + round-trip status),
* expose a hermetic recorded-fixture mode for CI-without-Java.

The manifest contract for ``windtrader`` is a dict:

    "windtrader": {"status": "valid", "version": "0.2.0", "exit_code": 0,
                   "roundtrip_status": "valid", "roundtrip_exit": 0}

A legacy ``windtrader`` value of ``"unverified"`` (a bare string) means no
verdict recorded yet — never treated as valid, and downgraded to
``"unknown"`` by the ratchet with a warning (see ``ratchet_regressions``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from sysml2py_lab.validate import windtrader as wt

# Cache of the last full-corpus round-trip pass, keyed by (corpus_dir, pkg).
# `--generated --record` would otherwise run the O(corpus) generator pass
# TWICE in one CLI invocation (once in compute_verdicts, once in
# write_fixture_file) — r2 W2.
_RT_CACHE: dict[tuple[str, str | None, str], tuple[dict[str, str], dict[str, str]]] = {}


def _load_manifest(corpus_dir: Path) -> dict[str, Any]:
    manifest_path = corpus_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"corpus manifest missing: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_manifest(corpus_dir: Path) -> dict[str, Any]:
    """Public alias: load the corpus manifest (files + metadata)."""
    return _load_manifest(corpus_dir)


def save_manifest(corpus_dir: Path, manifest: dict[str, Any]) -> None:
    manifest_path = corpus_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _roundtrip_outputs(corpus_dir: Path, generated_pkg: Path | None):
    """Compute the generator-emitted (round-trip) text for every corpus file.

    Reuses the proven regress composition: text -> IR -> generated classes ->
    get_definition() -> dump().  Returns ``(texts, errs)`` where ``texts`` is
    {rel: emitted text} (a file whose round-trip succeeded) and ``errs`` is
    {rel: reason} for files whose round-trip FAILED to produce text at all
    (a *generator* crash, NOT a windtrader verdict — surfaced as the
    ``emit_error`` bucket, issue-#12 r1 B5).  When ``generated_pkg`` is None,
    both are empty (inputs-only validation).
    """
    if generated_pkg is None:
        return {}, {}
    # r3 W2-K: key on mtime so an edited corpus in the same process can't
    # reuse stale emitted text.
    key = (str(corpus_dir.resolve()), str(generated_pkg),
           str(max((p.stat().st_mtime_ns for p in corpus_dir.rglob("*.sysml")), default=0)))
    if key in _RT_CACHE:
        return _RT_CACHE[key]
    from sysml2py_lab.regress import _ensure_pkg, _drop_pkg, _ir_to_json, dump_from_definition
    try:
        _ensure_pkg(generated_pkg)  # puts <pkg>/src (or <pkg>/sysml2py/src) on sys.path
        import sysml2py  # type: ignore[import-not-found]
        from sysml2py_lab.ir import parse_ir

        texts: dict[str, str] = {}
        errs: dict[str, str] = {}
        manifest = _load_manifest(corpus_dir)
        for rel in sorted(manifest.get("files", {})):
            p = corpus_dir / rel
            if not p.exists():
                errs[rel] = "file missing on disk"
                continue
            try:
                src = p.read_text(encoding="utf-8")
                root = parse_ir(src)
                tree = sysml2py.Node.from_ir(_ir_to_json(root))
                texts[rel] = dump_from_definition(tree.get_definition())
            except Exception as exc:  # noqa: BLE001
                # A generator crash is NOT a windtrader verdict.  Record the
                # reason so the operator can see WHY round-trip failed.
                errs[rel] = f"{type(exc).__name__}: {exc}"
        _RT_CACHE[key] = (texts, errs)
        return texts, errs
    finally:
        # Leave the process clean (r2 W2): drop the generated pkg from
        # sys.path and purge cached sysml2py modules so a later run in the
        # same process (or a test) can't accidentally reuse a stale tree.
        _drop_pkg(generated_pkg)


def _manifest_names(corpus_dir: Path) -> list[str]:
    return sorted(load_manifest(corpus_dir).get("files", {}).keys())


def compute_verdicts(
    corpus_dir: Path,
    *,
    version: str = wt.DEFAULT_VERSION,
    generated_pkg: Path | None = None,
    recorded: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute per-file windtrader verdicts for the whole corpus.

    For each file we validate:
      1. the RAW corpus text,
      2. (if pkg supplied) the round-trip emitted text.

    Verdict keys: ``input_status``, ``input_exit``, ``roundtrip_status``
    (absent when no pkg), ``version``, ``diagnostics`` (input-side only),
    and ``emit_error`` (the round-trip failure reason, when a generator
    crash prevented emission).

    Statuses: ``valid`` / ``invalid`` / ``adapter_error`` (windtrader could
    not run) and, separately, ``emit_error`` (our generator crashed while
    producing the text — a DIFFERENT failure axis from windtrader).
    """
    files = _manifest_names(corpus_dir)
    if not files:
        raise ValueError(f"corpus manifest declares no files: {corpus_dir}")

    # Warn if on-disk .sysml files exist but aren't in the manifest — they
    # are invisible to the gate entirely (r2 W2).
    manifest = load_manifest(corpus_dir)
    on_disk = sorted(
        p.relative_to(corpus_dir).as_posix()
        for p in corpus_dir.rglob("*.sysml")
    )
    missing_from_manifest = [rel for rel in on_disk if rel not in manifest.get("files", {})]
    if missing_from_manifest:
        print(f"WARNING: {len(missing_from_manifest)} .sysml file(s) on disk not in "
              f"manifest (invisible to the gate): {missing_from_manifest[:3]}",
              file=sys.stderr)

    # Hoist the full-corpus round-trip pass ABOVE the per-file loop (r1 W1-1:
    # calling it inside the loop was O(n²) — n full-corpus passes).
    rt_texts, rt_errs = _roundtrip_outputs(corpus_dir, generated_pkg)

    out: dict[str, dict[str, Any]] = {}
    for rel in files:
        p = corpus_dir / rel
        if not p.exists():
            out[rel] = {"input_status": "adapter_error", "input_exit": None,
                        "details": "file missing on disk"}
            continue
        src = p.read_text(encoding="utf-8")
        try:
            iv = wt.validate_text(src, version=version, recorded=recorded)
        except wt.AdapterError as exc:
            # Per-file resilience: one file's tooling failure is recorded as
            # adapter_error and summarized — it does NOT abort the corpus
            # pass (which would hide verdicts for the other files).
            out[rel] = {"input_status": "adapter_error", "input_exit": None,
                        "diagnostics": [str(exc)]}
            continue
        entry: dict[str, Any] = {
            "input_status": iv.status,
            "input_exit": iv.exit_code,
            "diagnostics": iv.diagnostics,
        }
        if generated_pkg is not None:
            if rel in rt_errs:
                # Our generator crashed — distinct from windtrader failing.
                entry["roundtrip_status"] = "emit_error"
                entry["roundtrip_exit"] = None
                entry["emit_error"] = rt_errs[rel]
            elif rel in rt_texts:
                rt_text = rt_texts[rel]
                try:
                    rv = wt.validate_text(rt_text, version=version, recorded=recorded)
                except wt.AdapterError as exc:
                    entry["roundtrip_status"] = "adapter_error"
                    entry["roundtrip_exit"] = None
                    entry.setdefault("details", str(exc))
                else:
                    entry["roundtrip_status"] = rv.status
                    entry["roundtrip_exit"] = rv.exit_code
                    entry["roundtrip_diagnostics"] = rv.diagnostics
            else:
                entry["roundtrip_status"] = "adapter_error"
                entry["roundtrip_exit"] = None
                entry.setdefault("details", "round-trip output unavailable")
        entry["version"] = version
        out[rel] = entry
    return out


def summarize(verdicts: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Aggregate per-file verdicts into non-overlapping gate-able counters.

    Each of the four failure axes is counted SEPARATELY so they partition
    the files (r1 W1-4): a file can be input-invalid AND round-trip emit_error
    simultaneously, but appears in each bucket only for its own axis.
    """
    n = len(verdicts)
    return {
        "files": n,
        "input_valid": sum(1 for e in verdicts.values() if e.get("input_status") == "valid"),
        "input_invalid": sum(1 for e in verdicts.values() if e.get("input_status") == "invalid"),
        "input_adapter_errors": sum(1 for e in verdicts.values() if e.get("input_status") == "adapter_error"),
        "roundtrip_valid": sum(1 for e in verdicts.values() if e.get("roundtrip_status") == "valid"),
        "roundtrip_invalid": sum(1 for e in verdicts.values() if e.get("roundtrip_status") == "invalid"),
        "roundtrip_adapter_errors": sum(1 for e in verdicts.values() if e.get("roundtrip_status") == "adapter_error"),
        "roundtrip_emit_errors": sum(1 for e in verdicts.values() if e.get("roundtrip_status") == "emit_error"),
        "roundtrip_present": sum(1 for e in verdicts.values() if "roundtrip_status" in e),
    }


def manifest_windtrader_field(entry: dict[str, Any]) -> dict[str, Any]:
    """Shrink a computed-verdict entry into the manifest field value.

    Records the input verdict plus, when present, the round-trip (generator
    output) verdict — AC#2: generator-emitted text is validated, and the
    verdict is recorded per file.
    """
    field: dict[str, Any] = {
        "status": entry.get("input_status", "unverified"),
        "version": entry.get("version", wt.DEFAULT_VERSION),
        "exit_code": entry.get("input_exit"),
    }
    if "roundtrip_status" in entry:
        field["roundtrip_status"] = entry.get("roundtrip_status")
        field["roundtrip_exit"] = entry.get("roundtrip_exit")
    return field


def ratchet_regressions(
    corpus_dir: Path,
    verdicts: dict[str, dict[str, Any]],
) -> list[str]:
    """Return files that REGRESSED valid → non-valid vs the committed
    manifest baseline (ratchet semantics), across BOTH input and round-trip.

    The committed manifest ``windtrader.status`` / ``roundtrip_status`` is
    the baseline.  A file that was ``valid`` on either axis but is no longer
    valid is a regression the gate must block.  Files already recorded
    ``invalid`` in the baseline are tracked WIP and are NOT regressions (an
    improvement flipping them to valid is welcome).

    A legacy bare-string baseline (``"unverified"``) is downgraded to
    ``"unknown"`` with a warning — it is NOT a valid baseline, and the file
    is not treated as having-regressed (it has none), though AC#3's
    "unverified" check (in the CLI) still blocks it.
    """
    manifest = load_manifest(corpus_dir)
    regressions: list[str] = []
    for rel, entry in verdicts.items():
        baseline = manifest.get("files", {}).get(rel, {}).get("windtrader", {})
        if not isinstance(baseline, dict):
            print(f"WARNING: file {rel} has a non-dict windtrader baseline "
                  f"{baseline!r}; treating as unknown (not a regression, but "
                  f"not a valid baseline either)", file=sys.stderr)
        for axis, cur_attr, base_attr in (
            ("input", "input_status", "status"),
            ("roundtrip", "roundtrip_status", "roundtrip_status"),
        ):
            base = baseline.get(base_attr) if isinstance(baseline, dict) else "unknown"
            cur = entry.get(cur_attr)
            if cur is None or axis == "roundtrip" and cur == "emit_error":
                # No round-trip axis on this entry, or our generator crashed
                # (handled as emit_error, gated separately) — skip.
                continue
            if base == "valid" and cur != "valid":
                regressions.append(
                    f"{rel}: {axis} valid -> {cur} (baseline had a passing verdict)"
                )
    return regressions


def roundtrip_regressions(verdicts: dict[str, dict[str, Any]]) -> list[str]:
    """Return generator-defect regressions: input valid but round-trip not.

    A file whose INPUT the oracle accepts but whose generator OUTPUT it
    rejects is unambiguously a lab defect TODAY (issue-#12 r1 B1).  This is
    the invariant that needs no baseline.
    """
    return [
        f"{rel}: input=valid but roundtrip={e.get('roundtrip_status')}"
        for rel, e in verdicts.items()
        if e.get("input_status") == "valid"
        and "roundtrip_status" in e
        and e.get("roundtrip_status") not in ("valid", "emit_error")  # r3 W2-F
    ]


def emit_error_files(verdicts: dict[str, dict[str, Any]]) -> list[str]:
    """Return files whose generator round-trip crashed (emit_error)."""
    return [
        f"{rel}: {e.get('emit_error')}"
        for rel, e in verdicts.items()
        if e.get("roundtrip_status") == "emit_error"
    ]


def unbaselined_files(
    corpus_dir: Path,
    verdicts: dict[str, dict[str, Any]],
) -> list[str]:
    """Return files with NO recorded baseline that are not valid (AC#3).

    A new/never-recorded file must have a PASSING verdict to enter the
    corpus (issue #12 AC#3).  Derived from the manifest baseline — not from
    the verdict (which never emits ``"unverified"``; r1 B2).  A file with no
    dict baseline (new or legacy "unverified") whose current input is not
    valid is flagged.
    """
    manifest = load_manifest(corpus_dir)
    out: list[str] = []
    for rel, e in verdicts.items():
        baseline = manifest.get("files", {}).get(rel, {}).get("windtrader")
        has_baseline = isinstance(baseline, dict) and baseline.get("status") in ("valid", "invalid")
        if not has_baseline and e.get("input_status") != "valid":
            out.append(f"{rel}: no recorded baseline and input={e.get('input_status')}")
    return out


def update_manifest_verdicts(
    corpus_dir: Path,
    verdicts: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    """Write input verdicts into manifest files' ``windtrader`` field.

    Returns (valid_count, invalid_count) over the INPUT verdicts, so the
    gate can require valid_count == files.
    """
    manifest = load_manifest(corpus_dir)
    valid = invalid = 0
    for rel, entry in verdicts.items():
        if rel not in manifest.get("files", {}):
            continue
        manifest["files"][rel]["windtrader"] = manifest_windtrader_field(entry)
        if entry.get("input_status") == "valid":
            valid += 1
        elif entry.get("input_status") == "invalid":
            invalid += 1
    save_manifest(corpus_dir, manifest)
    return valid, invalid


def build_fixture_dict(
    corpus_dir: Path,
    verdicts: dict[str, dict[str, Any]],
    *,
    version: str = wt.DEFAULT_VERSION,
    rt_texts: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Single shared builder for ``validate-fixtures.json`` (r2 W1-1).

    Records one entry per input digest and (when ``rt_texts`` supplied, i.e.
    a generated package was used) one per round-trip digest — so offline
    ``--offline --generated`` replay covers AC#2.  Round-trip diagnostics
    are preserved (r2 W1-3): a fixture for the emitted text carries the
    oracle's diagnostics for that text, never the input's.

    Refuses (adapter_error / emit_error present): baking a broken verdict
    into the trust anchor would corrupt it.
    """
    for rel, e in verdicts.items():
        bad = (e.get("input_status"), e.get("roundtrip_status"))
        if any(s in ("adapter_error", "emit_error") for s in bad if s is not None):
            raise wt.AdapterError(
                f"refusing to build fixtures: adapter/emit errors present "
                f"({rel}: {bad})"
            )
    fx: dict[str, dict[str, Any]] = {}
    for rel, e in verdicts.items():
        p = corpus_dir / rel
        src = p.read_text(encoding="utf-8") if p.exists() else ""
        fx[wt.digest_text(src, version)] = {
            "status": e.get("input_status", "adapter_error"),
            "exit_code": e.get("input_exit"),
            "version": version,
            "diagnostics": e.get("diagnostics", []),
            "file": rel,
        }
        if rt_texts is not None and rel in rt_texts:
            rt = rt_texts[rel]
            fx[wt.digest_text(rt, version)] = {
                "status": e.get("roundtrip_status", "adapter_error"),
                "exit_code": e.get("roundtrip_exit"),
                "version": version,
                "diagnostics": e.get("roundtrip_diagnostics", []),
                "file": rel,
            }
    return fx


def write_fixture_file(
    corpus_dir: Path,
    verdicts: dict[str, dict[str, Any]],
    *,
    dest: Path,
    generated_pkg: Path | None,
    version: str = wt.DEFAULT_VERSION,
) -> dict[str, dict[str, Any]]:
    """Record verified fixtures to ``dest`` after the gate has passed.

    Uses the single shared builder; with a generated package the round-trip
    texts are recovered (so the fixture set includes round-trip digests).
    """
    rt_texts, _ = _roundtrip_outputs(corpus_dir, generated_pkg)
    fx = build_fixture_dict(corpus_dir, verdicts, version=version, rt_texts=rt_texts)
    wt.save_fixtures(dest, fx)
    return fx
