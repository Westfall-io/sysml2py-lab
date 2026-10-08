"""Casting for issue #9: corpus -> IR -> generated classes -> dump -> canonical.

Proves the generated AST classes faithfully represent every corpus model:
parse each .sysml with the lab IR, build a generated-class tree via the
membership dispatch (``Node.from_ir``), dump() it, and canonical-compare
against the original source (the issue-#8 round-trip gate, now over
generated classes).

Load-bearing (issue-#9 review C1): the cast exits nonzero if any aliased IR
kind collapses to ``Unsupported`` (the generated classes must be exercised,
not bypassed), and prints an instantiated-class histogram.
"""
from __future__ import annotations

import sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
GEN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else LAB / "out" / "sysml2py"
CORPUS = LAB / "corpus"

sys.path.insert(0, str(LAB / "src"))
sys.path.insert(0, str(GEN))

import sysml2py  # noqa: E402

from sysml2py_lab.ir import ir_to_json, parse_ir  # noqa: E402
from sysml2py_lab.normalize import canonical_equals  # noqa: E402


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


def walk(node) -> list[object]:
    out = [node]
    for c in getattr(node, "children", []) or []:
        out.extend(walk(c))
    return out


def main() -> None:
    files = sorted(CORPUS.rglob("*.sysml"))
    passed = 0
    failed = 0
    waivers: list[tuple[str, str]] = []
    histogram: dict[str, int] = {}
    unsupported_for_aliased: set[str] = set()
    for f in files:
        ok, why = cast_file(f)
        if ok:
            passed += 1
        else:
            failed += 1
            waivers.append((str(f.relative_to(CORPUS)), why))
            continue
        # Load-bearing: count instantiated classes, flag aliased->Unsupported.
        try:
            root = parse_ir(f.read_text(encoding="utf-8"))
            tree = sysml2py.Node.from_ir(ir_to_json(root))
            for node in walk(tree):
                histogram[type(node).__name__] = histogram.get(type(node).__name__, 0) + 1
                ir_kind = getattr(node, "ir_kind", None)
                if (
                    type(node).__name__ == "Unsupported"
                    and ir_kind in sysml2py.IR_KIND_ALIASES
                    and sysml2py.IR_KIND_ALIASES[ir_kind] != "Unsupported"
                ):
                    unsupported_for_aliased.add(f"{f.name}:{ir_kind}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            waivers.append((str(f.relative_to(CORPUS)), f"histogram walk failed: {exc}"))
    print(f"CAST: {passed}/{len(files)} passed, {failed} failed")
    if unsupported_for_aliased:
        print(f"UNSUPPORTED-FOR-ALIASED: {sorted(unsupported_for_aliased)}")
        failed += len(unsupported_for_aliased)
    print(f"INSTANTIATED-CLASSES: {len(histogram)} distinct")
    aliased = {v for v in sysml2py.IR_KIND_ALIASES.values() if v != "Unsupported"}
    missing = sorted(aliased - set(histogram))
    if missing:
        print(f"MISSING-ALIASED-CLASSES: {missing}")
        failed += len(missing)
    top = sorted(histogram.items(), key=lambda kv: -kv[1])[:12]
    print("TOP:", ", ".join(f"{k}={v}" for k, v in top))
    if waivers:
        print("WAIVERS:")
        for name, why in waivers:
            print(f"  - {name}: {why}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
