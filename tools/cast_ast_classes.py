"""Casting for issue #9: corpus -> IR -> generated classes -> dump -> canonical.

Proves the generated AST classes faithfully represent every corpus model:
parse each .sysml with the lab IR, build a generated-class tree via the
membership dispatch, dump() it, and canonical-compare against the original
source (the issue-#8 round-trip gate, now over generated classes).

Waivers (per acceptance): files whose parse produces no IR (fidelity opaque)
are recorded, not failed.
"""
from __future__ import annotations

import sys
from pathlib import Path

LAB = Path("/opt/data/work/jasper-quillweld/sysml2py-lab")
GEN = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / "/tmp/gen9e"
CORPUS = LAB / "corpus"

sys.path.insert(0, str(LAB / "src"))
sys.path.insert(0, str(GEN))

import sysml2py

from sysml2py_lab.ir import ir_to_json, parse_ir
from sysml2py_lab.normalize import canonical_equals


def cast_file(path: Path) -> tuple[bool, str]:
    src = path.read_text(encoding="utf-8")
    try:
        root = parse_ir(src)
        j = ir_to_json(root)
    except Exception as exc:  # noqa: BLE001
        return (False, f"parse/IR failed: {exc.__class__.__name__}: {exc}")
    # build generated tree from IR JSON
    try:
        if not isinstance(j, dict):
            return (False, f"IR JSON root not a dict: {type(j).__name__}")
        # Lift the whole IR tree into generated nodes via from_ir (which maps
        # coarse IR kinds through IR_KIND_ALIASES and recurses), then dump()
        # the generated tree — proving the classes + dispatch round-trip the
        # source losslessly.
        tree = sysml2py.Node.from_ir(j)
        dumped = tree.dump()
    except Exception as exc:  # noqa: BLE001
        return (False, f"generated-class build failed: {exc.__class__.__name__}: {exc}")
    if not canonical_equals(dumped, src):
        return (False, "canonical mismatch between dumped and source")
    return (True, "")


def main() -> None:
    files = sorted(CORPUS.rglob("*.sysml"))
    passed = 0
    failed = 0
    waivers: list[tuple[str, str]] = []
    for f in files:
        ok, why = cast_file(f)
        if ok:
            passed += 1
        else:
            failed += 1
            waivers.append((str(f.relative_to(CORPUS)), why))
    print(f"CAST: {passed}/{len(files)} passed, {failed} failed")
    if waivers:
        print("WAIVERS:")
        for name, why in waivers:
            print(f"  - {name}: {why}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
