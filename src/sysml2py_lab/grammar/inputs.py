from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SOURCES_FILENAME = "SOURCES.json"
MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True)
class GrammarInputManifest:
    """A records checksums for vendored grammar input files."""

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


def verify_inputs(grammar_dir: Path) -> GrammarInputManifest:
    """
    Verify vendored grammar input files against the recorded manifest.

    - If no manifest exists, record one (first run).
    - If a file's sha256 differs from the manifest, raise (drift detected).
    Returns the (possibly newly-written) manifest.
    """
    grammar_dir = grammar_dir.expanduser().resolve()
    files = list(_iter_grammar_files(grammar_dir))

    manifest_path = grammar_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        manifest = load_manifest(grammar_dir)
        entries = manifest.entries
    else:
        entries = {}

    new_entries: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(grammar_dir).as_posix()
        digest = _sha256(f)
        if rel in entries:
            if entries[rel] != digest:
                raise RuntimeError(
                    f"grammar input drift detected for {rel}: recorded "
                    f"{entries[rel][:12]}... but file sha256 is {digest[:12]}..."
                )
        new_entries[rel] = digest

    # Also verify that no recorded entries are missing (file was removed)
    for rel in entries:
        if rel not in new_entries:
            raise RuntimeError(f"grammar input {rel} recorded in manifest is missing from {grammar_dir}")

    manifest = GrammarInputManifest(entries=new_entries)
    write_manifest(grammar_dir, manifest)
    return manifest
