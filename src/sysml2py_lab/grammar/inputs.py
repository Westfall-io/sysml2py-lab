from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SOURCES_FILENAME = "SOURCES.json"
MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True)
class GrammarInputManifest:
    """A record of checksums for vendored grammar input files."""

    entries: dict[str, str] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _iter_grammar_files(grammar_dir: Path):
    """Yield .xtext / .tx files under the grammar inputs dir (recursive)."""
    for ext in ("*.xtext", "*.tx"):
        yield from sorted(grammar_dir.rglob(ext))


def write_manifest(grammar_dir: Path, manifest: GrammarInputManifest) -> None:
    """Write manifest.json into the grammar inputs dir."""
    (grammar_dir / MANIFEST_FILENAME).write_text(
        json.dumps({"version": 1, "entries": manifest.entries}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_manifest(grammar_dir: Path) -> GrammarInputManifest:
    """Load manifest.json from the grammar inputs dir."""
    p = grammar_dir / MANIFEST_FILENAME
    if not p.exists():
        raise FileNotFoundError(f"no {MANIFEST_FILENAME} in {grammar_dir}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return GrammarInputManifest(entries=data.get("entries", {}))


def record_manifest(grammar_dir: Path) -> GrammarInputManifest:
    """Record sha256 hashes for every grammar file into manifest.json.

    Explicitly writes the manifest (used by `inputs record`).  Read-only
    verification never writes the manifest.
    """
    grammar_dir = grammar_dir.expanduser().resolve()
    entries: dict[str, str] = {}
    for f in _iter_grammar_files(grammar_dir):
        rel = f.relative_to(grammar_dir).as_posix()
        entries[rel] = _sha256(f)
    manifest = GrammarInputManifest(entries=entries)
    write_manifest(grammar_dir, manifest)
    return manifest


def verify_inputs(grammar_dir: Path) -> GrammarInputManifest:
    """Read-only verification of vendored grammar files against the manifest.

    - Raises FileNotFoundError if no manifest exists.
    - Raises RuntimeError on drift (a file's sha256 differs from the manifest).
    - Raises RuntimeError if a file is present that is not recorded.
    - Raises RuntimeError if a recorded file is missing.

    Does NOT modify the manifest (provenance gate must not bless new files).
    """
    grammar_dir = grammar_dir.expanduser().resolve()
    manifest_path = grammar_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"no {MANIFEST_FILENAME} in {grammar_dir}; run `inputs record` first"
        )
    manifest = load_manifest(grammar_dir)
    entries = manifest.entries

    files = {f.relative_to(grammar_dir).as_posix(): f for f in _iter_grammar_files(grammar_dir)}
    # any file present but not recorded -> fail (don't bless new files)
    for rel in files:
        if rel not in entries:
            raise RuntimeError(
                f"grammar input {rel} is not recorded in {MANIFEST_FILENAME}; "
                f"run `inputs record` to bless it"
            )
    # any recorded entry missing -> fail
    for rel in entries:
        if rel not in files:
            raise RuntimeError(
                f"grammar input {rel} recorded in manifest is missing from {grammar_dir}"
            )
    # drift check
    for rel, f in files.items():
        digest = _sha256(f)
        if entries[rel] != digest:
            raise RuntimeError(
                f"grammar input drift detected for {rel}: recorded "
                f"{entries[rel][:12]}... but file sha256 is {digest[:12]}..."
            )
    return manifest
