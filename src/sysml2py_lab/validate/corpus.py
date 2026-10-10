"""Corpus-level validation orchestration (issue #12).

Composes the per-text windtrader adapter with the corpus manifest:
* validate every corpus file's RAW text (the input that must be valid),
* when a generated package is supplied, ALSO validate the round-trip output
  (the generator-emitted sysml2py text — AC: round-trip output is validated,
  not just the input),
* record per-file verdicts into the manifest ``windtrader`` field
  (``{"status": ..., "version": ..., "exit_code": ...}``),
* expose a hermetic recorded-fixture mode for CI-without-Java.

The manifest contract for ``windtrader`` is JSON-lite:

    "windtrader": {"status": "valid", "version": "0.2.0", "exit_code": 0}

A ``windtrader`` value of ``"unverified"`` (or missing) means no verdict
recorded yet — never treated as valid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sysml2py_lab.validate import windtrader as wt


def _load_manifest(corpus_dir: Path) -> dict[str, Any]:
    manifest_path = corpus_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"corpus manifest missing: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_manifest(corpus_dir: Path, manifest: dict[str, Any]) -> None:
    manifest_path = corpus_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _roundtrip_outputs(corpus_dir: Path, generated_pkg: Path | None) -> dict[str, str]:
    """Return {manifest_rel: round-trip emitted text} when a pkg is present.

    Reuses the exact composition proven in regress.roundtrip: text -> IR ->
    generated classes -> get_definition() -> dump().  Returns {} when no
    pkg is supplied (inputs-only validation).
    """
    if generated_pkg is None:
        return {}
    import sysml2py  # type: ignore[import-not-found]
    from sysml2py_lab.ir import parse_ir
    from sysml2py_lab.regress import _ir_to_json, dump_from_definition

    out: dict[str, str] = {}
    manifest = _load_manifest(corpus_dir)
    for rel in sorted(manifest.get("files", {})):
        p = corpus_dir / rel
        if not p.exists():
            continue
        try:
            src = p.read_text(encoding="utf-8")
            root = parse_ir(src)
            tree = sysml2py.Node.from_ir(_ir_to_json(root))
            out[rel] = dump_from_definition(tree.get_definition())
        except Exception:  # noqa: BLE001
            # A round-trip failure is not a windtrader verdict: leave the
            # entry absent so the caller reports adapter_error distinctly.
            out[rel] = ""
    return out


def _manifest_names(corpus_dir: Path) -> list[str]:
    return sorted(_load_manifest(corpus_dir).get("files", {}).keys())


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
    (absent when no pkg), ``version``, ``diagnostics`` (input-side only).
    """
    files = _manifest_names(corpus_dir)
    if not files:
        raise ValueError(f"corpus manifest declares no files: {corpus_dir}")
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
            rts = _roundtrip_outputs(corpus_dir, generated_pkg)
            rt_text = rts.get(rel, "")
            if rt_text:
                try:
                    rv = wt.validate_text(rt_text, version=version, recorded=recorded)
                except wt.AdapterError as exc:
                    entry["roundtrip_status"] = "adapter_error"
                    entry["roundtrip_exit"] = None
                    entry.setdefault("details", str(exc))
                else:
                    entry["roundtrip_status"] = rv.status
                    entry["roundtrip_exit"] = rv.exit_code
            else:
                entry["roundtrip_status"] = "adapter_error"
                entry["roundtrip_exit"] = None
                entry.setdefault("details", "round-trip output unavailable")
        entry["version"] = version
        out[rel] = entry
    return out


def summarize(verdicts: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Aggregate per-file verdicts into gate-able counters.

    ``pass`` counts files whose INPUT validated (status valid) — plus, when
    round-trip is present, files whose ROUND-TRIP ALSO validated.
    """
    n = len(verdicts)
    input_ok = sum(1 for e in verdicts.values() if e.get("input_status") == "valid")
    roundtrip_ok = sum(
        1 for e in verdicts.values() if e.get("roundtrip_status") == "valid"
    )
    roundtrip_present = sum(
        1 for e in verdicts.values() if "roundtrip_status" in e
    )
    adapter_err = sum(
        1 for e in verdicts.values()
        if e.get("input_status") == "adapter_error"
        or e.get("roundtrip_status") == "adapter_error"
    )
    invalid = sum(
        1 for e in verdicts.values()
        if e.get("input_status") == "invalid"
        or e.get("roundtrip_status") == "invalid"
    )
    return {
        "files": n,
        "input_valid": input_ok,
        "roundtrip_valid": roundtrip_ok,
        "roundtrip_present": roundtrip_present,
        "invalid": invalid,
        "adapter_errors": adapter_err,
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
    """Return files that REGRESSED valid → invalid/adapter vs the committed
    manifest baseline (ratchet semantics).

    The committed manifest ``windtrader.status`` is the baseline.  A file
    that was ``valid`` but is no longer valid (now ``invalid`` or
    ``adapter_error``) is a regression the gate must block.  Files already
    recorded ``invalid`` in the baseline are tracked WIP and are NOT
    regressions (an improvement flipping them to valid is welcome).
    """
    manifest = _load_manifest(corpus_dir)
    regressions: list[str] = []
    for rel, entry in verdicts.items():
        baseline = manifest.get("files", {}).get(rel, {}).get("windtrader", {})
        base_status = baseline.get("status", "unknown") if isinstance(baseline, dict) else "unknown"
        cur = entry.get("input_status", "adapter_error")
        if base_status == "valid" and cur != "valid":
            regressions.append(
                f"{rel}: valid -> {cur} (baseline had a passing verdict)"
            )
    return regressions


def update_manifest_verdicts(
    corpus_dir: Path,
    verdicts: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    """Write input verdicts into manifest files' ``windtrader`` field.

    Returns (valid_count, invalid_count) over the INPUT verdicts, so the
    gate can require valid_count == files.
    """
    manifest = _load_manifest(corpus_dir)
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
