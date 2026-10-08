from __future__ import annotations

"""Corpus intake and management (issue #8).

The corpus is a provenance-tracked set of SysML v2 example files under
``corpus/<source>/`` with a ``corpus/manifest.json`` recording per file:

  - provenance: source name/URL/tag, license
  - integrity: sha256 of the file bytes
  - windtrader status (``unverified`` until issue #12 wires the adapter)
  - fidelity: modelled/partial/opaque node counts from the IR (issue #7),
    plus which node kinds the file exercises
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .ir import parse_ir, ir_fidelity_summary, _kind_vocab_from_model

MANIFEST_NAME = "manifest.json"


@dataclass
class CorpusEntry:
    """One corpus file's manifest entry (the mutable fields)."""

    sha256: str = ""
    windtrader: str = "unverified"
    fidelity_modelled: int = 0
    fidelity_partial: int = 0
    fidelity_opaque: int = 0
    node_kinds: list[str] = field(default_factory=list)
    source: str = ""
    source_url: str = ""
    license: str = ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_corpus_files(corpus_dir: Path) -> list[Path]:
    """All .sysml files under `corpus_dir` (sorted for determinism)."""
    return sorted(p for p in corpus_dir.rglob("*.sysml") if p.is_file())


def load_manifest(corpus_dir: Path) -> dict:
    mf = corpus_dir / MANIFEST_NAME
    if not mf.exists():
        return {"format": 1, "files": {}}
    return json.loads(mf.read_text(encoding="utf-8"))


def write_manifest(corpus_dir: Path, manifest: dict) -> None:
    mf = corpus_dir / MANIFEST_NAME
    # sorted keys for deterministic diffs
    mf.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def analyze_file(path: Path, model_path: str | None = None) -> tuple[dict, dict]:
    """Parse `path` to IR; return (node_kinds_by_name, fidelity_summary)."""
    src = path.read_text(encoding="utf-8")
    root = parse_ir(src, model_path=model_path)
    kinds: set[str] = set()
    for n in root.walk():
        if n.kind not in ("root", "brace_open", "brace_close", "block", "comment"):
            kinds.add(n.kind)
    summ = ir_fidelity_summary(root, source=src)
    return {"node_kinds": sorted(kinds)}, summ


def add_file(
    corpus_dir: Path,
    src: Path,
    *,
    source: str,
    source_url: str = "",
    license: str = "",
    dest_subdir: str = "local",
    model_path: str | None = None,
) -> str:
    """Add a .sysml file to the corpus, updating the manifest.

    Returns the manifest-relative path of the added file.
    """
    corpus_dir = corpus_dir.expanduser().resolve()
    dest = corpus_dir / dest_subdir
    dest.mkdir(parents=True, exist_ok=True)
    rel = f"{dest_subdir}/{src.name}"
    out = corpus_dir / rel
    # Analyze BEFORE writing (review W10): if analysis raises, no orphan file
    # is left behind under the corpus with a missing manifest entry.
    body = src.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    kinds, summ = analyze_file(src, model_path=model_path)
    out.write_bytes(body)
    manifest = load_manifest(corpus_dir)

    entry = manifest["files"].get(rel, {})
    entry.update(
        {
            "source": source,
            "source_url": source_url,
            "license": license,
            "sha256": digest,
            "windtrader": entry.get("windtrader", "unverified"),
            "fidelity": {
                "modelled": summ["node_counts"]["modelled"],
                "partial": summ["node_counts"]["partial"],
                "opaque": summ["node_counts"]["opaque"],
            },
            "node_kinds": kinds["node_kinds"],
        }
    )
    manifest["files"][rel] = entry
    write_manifest(corpus_dir, manifest)
    return rel


def verify_corpus(corpus_dir: Path, *, model_path: str | None = None) -> dict:
    """Verify integrity + parseability of every corpus file.

    Returns a report: per-file status with any errors.  A file fails if its
    sha256 differs from the manifest OR it no longer parses to IR.
    """
    corpus_dir = corpus_dir.expanduser().resolve()
    manifest = load_manifest(corpus_dir)
    files = iter_corpus_files(corpus_dir)
    report = {"checked": 0, "ok": [], "errors": []}
    for p in files:
        rel = p.relative_to(corpus_dir).as_posix()
        report["checked"] += 1
        entry = manifest["files"].get(rel)
        if entry is None:
            report["errors"].append(f"{rel}: not in manifest")
            continue
        digest = _sha256(p)
        if digest != entry.get("sha256"):
            report["errors"].append(f"{rel}: sha256 mismatch")
            continue
        try:
            analyze_file(p, model_path=model_path)
        except Exception as e:  # noqa: BLE001 — report any parse failure
            report["errors"].append(f"{rel}: parse failed: {e}")
            continue
        report["ok"].append(rel)
    # also flag manifest entries whose file is missing
    for rel in manifest["files"]:
        if not (corpus_dir / rel).exists():
            report["errors"].append(f"{rel}: missing file")
    return report


def corpus_stats(corpus_dir: Path, *, model_path: str | None = None) -> dict:
    """Aggregate node-kind coverage against the relationship model.

    Returns: `files_with_kind` — the number of corpus files containing each
    node kind (set-based; a kind present in 60 files maxes at 60), the set of
    model kinds with ZERO corpus coverage, and fidelity totals.  `unknown`
    (the opaque bucket) is excluded — it is not a vocabulary member.
    """
    vocab = set(_kind_vocab_from_model(model_path))
    model_loaded = bool(vocab)
    used: dict[str, int] = {}
    per_file: dict[str, list[str]] = {}
    fidelity_total = {"modelled": 0, "partial": 0, "opaque": 0}
    for p in iter_corpus_files(corpus_dir):
        kinds, summ = analyze_file(p, model_path=model_path)
        klist = [k for k in kinds["node_kinds"] if k != "unknown"]
        per_file[p.relative_to(corpus_dir).as_posix()] = klist
        for k in klist:
            used[k] = used.get(k, 0) + 1
        for f in fidelity_total:
            fidelity_total[f] += summ["node_counts"][f]
    # kinds the relationship model knows but the corpus never shows
    zero = sorted(vocab - set(used)) if model_loaded else []
    return {
        "files": len(per_file),
        "model_loaded": model_loaded,
        "files_with_kind": dict(sorted(used.items(), key=lambda kv: (-kv[1], kv[0]))),
        "kinds_zero_coverage": zero,
        "fidelity_totals": fidelity_total,
        "per_file": per_file,
    }
