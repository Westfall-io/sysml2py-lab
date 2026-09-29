#!/usr/bin/env python3
"""Fill SOURCES.json sha256 fields and verify_inputs against grammar_inputs/."""
import hashlib, json
from pathlib import Path

root = Path(__file__).resolve().parent.parent  # repo root (scripts/ -> repo root)
gdir = root / "grammar_inputs"

def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

# Fill SOURCES.json
sources = json.loads((gdir / "SOURCES.json").read_text(encoding="utf-8"))
for name, meta in sources["files"].items():
    f = gdir / name
    meta["sha256"] = sha256(f)
    meta["bytes"] = f.stat().st_size
    print(f"{name}: {meta['sha256'][:16]}... ({meta['bytes']} bytes)")
(gdir / "SOURCES.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
print("SOURCES.json updated")

# Run verify_inputs (writes manifest.json)
from sysml2py_lab.grammar.inputs import verify_inputs, load_manifest
m = verify_inputs(gdir)
print("manifest entries:", sorted(m.entries.keys()))
lm = load_manifest(gdir)
print("load roundtrip ok:", lm.entries == m.entries)
