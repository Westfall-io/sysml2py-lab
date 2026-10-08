"""Casting for issue #9: corpus -> IR -> generated classes -> dump -> canonical.

Proves the generated AST classes faithfully represent every corpus model:
parse each .sysml with the lab IR, lift the whole IR tree into generated
classes (``Node.from_ir``, body-context dispatch), dump() it, and
canonical-compare against the original source (the issue-#8 round-trip gate,
now over generated classes).

Load-bearing (issue-#9 review): the exit code fails if:
  - any file's dump does not canonical-equal its source, OR
  - any aliased IR kind collapses to ``Unsupported`` (per-file), OR
  - any aliased class is never instantiated across the corpus, OR
  - **no real owned-body context was ever used for dispatch** (i.e. the
    children.json membership table is dead — dispatch_member never consulted).

The generated classes + dispatch table are therefore exercised, not bypassed.
"""
from __future__ import annotations

import sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
GEN = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else LAB / "out" / "sysml2py" / "src"
CORPUS = LAB / "corpus"

sys.path.insert(0, str(LAB / "src"))
sys.path.insert(0, str(GEN))

import sysml2py

from sysml2py_lab.ir import ir_to_json, parse_ir
from sysml2py_lab.normalize import canonical_equals


def cast_file(path):
    """Return (ok, message, hist, unsup, dispatch_bodies) for one file."""
    src = path.read_text(encoding="utf-8")
    try:
        root = parse_ir(src)
        j = ir_to_json(root)
    except Exception as exc:  # noqa: BLE001
        return (False, f"parse/IR failed: {exc.__class__.__name__}: {exc}", {}, set(), set())
    if not isinstance(j, dict):
        return (False, f"IR JSON root not a dict: {type(j).__name__}", {}, set(), set())
    hist: dict[str, int] = {}
    unsup: set[str] = set()
    dispatch_bodies: set[str] = set()
    try:
        tree = sysml2py.Node.from_ir(j)
        dumped = tree.dump()
    except Exception as exc:  # noqa: BLE001
        return (False, f"generated-class build failed: {exc.__class__.__name__}: {exc}", {}, set(), set())
    if not canonical_equals(dumped, src):
        return (False, "canonical mismatch between dumped and source", {}, set(), set())
    stack = [tree]
    while stack:
        node = stack.pop()
        hist[type(node).__name__] = hist.get(type(node).__name__, 0) + 1
        ir_kind = getattr(node, "ir_kind", None)
        if (
            type(node).__name__ == "Unsupported"
            and ir_kind in sysml2py.IR_KIND_ALIASES
            and sysml2py.IR_KIND_ALIASES[ir_kind] != "Unsupported"
        ):
            unsup.add(f"{path.name}:{ir_kind}")
        body = getattr(node, "_dispatch_body", None)
        if body:
            dispatch_bodies.add(body)
        stack.extend(getattr(node, "children", []) or [])
    return (True, "", hist, unsup, dispatch_bodies)


def main() -> None:
    files = sorted(CORPUS.rglob("*.sysml"))
    passed = 0
    failed = 0
    waivers: list[tuple[str, str]] = []
    histogram: dict[str, int] = {}
    unsupported_for_aliased: set[str] = set()
    dispatch_bodies_all: set[str] = set()
    for f in files:
        ok, why, hist, unsup, bodies = cast_file(f)
        if ok:
            passed += 1
            for cls, n in hist.items():
                histogram[cls] = histogram.get(cls, 0) + n
            unsupported_for_aliased |= unsup
            dispatch_bodies_all |= bodies
        else:
            failed += 1
            waivers.append((str(f.relative_to(CORPUS)), why))
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
    # The dispatch table must actually be consulted (load-bearing): route
    # through a body context.  Fail if never used.
    real_bodies = {b for b in dispatch_bodies_all if b in getattr(sysml2py, "MEMBERSHIP_DISPATCH", {})}
    if not real_bodies:
        print("NO-DISPATCH-BODY-USED: the children.json membership table was never consulted")
        failed += 1
    else:
        print(f"DISPATCH-BODIES-USED: {sorted(real_bodies)[:8]}")
    top = sorted(histogram.items(), key=lambda kv: -kv[1])[:12]
    print("TOP:", ", ".join(f"{k}={v}" for k, v in top))
    if waivers:
        print("WAIVERS:")
        for name, why in waivers:
            print(f"  - {name}: {why}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
