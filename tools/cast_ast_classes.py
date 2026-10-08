"""Casting for issue #9: corpus -> IR -> generated classes -> dump -> canonical.

Proves the generated AST classes faithfully represent every corpus model:
parse each .sysml with the lab IR, lift the whole IR tree into generated
classes (``Node.from_ir`` — registry-authoritative lifting via
``IR_KIND_ALIASES`` + ``KIND_REGISTRY``), dump() it, and
canonical-compare against the original source (the issue-#8 round-trip gate,
now over generated classes).

Load-bearing (issue-#9 review): the exit code fails if:
  - any file's dump does not canonical-equal its source, OR
  - any aliased IR kind collapses to ``Unsupported`` (per-file), OR
  - any aliased class is never instantiated across the corpus.

The registry (KIND_REGISTRY) is authoritative for lifting IR kinds to
generated classes; children.json's ``dispatch_member`` is an advisory API,
not consulted by ``from_ir`` (the IR is too coarse for it to be
authoritative — see the decision record).  The gates claim exactly what is
true: the generated classes are exercised (histogram) and no aliased kind is
silently degraded (unsupported_for_aliased).
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

# Pinned expected set of aliased classes (C1): derived as a literal so that
# emptying IR_KIND_ALIASES cannot vacate the cast gates.
REQUIRED_ALIASED_CLASSES = frozenset(
    {
        "Package",
        "PartUsage",
        "AttributeUsage",
        "ActionUsage",
        "ItemUsage",
        "PortUsage",
        "StateUsage",
        "TransitionUsage",
        "ConstraintUsage",
        "InterfaceUsage",
        "ConnectionUsage",
        "OccurrenceUsage",
        "RequirementUsage",
        "UseCaseUsage",
        "ObjectiveRequirementUsage",
        "SubjectUsage",
        "ActorUsage",
        "Message",
        "Succession",
        "AliasMember",
        "Import",
        "Comment",
        "RootNamespace",
    }
)


def cast_file(path):
    """Return (ok, message, hist, unsup) for one file."""
    src = path.read_text(encoding="utf-8")
    try:
        root = parse_ir(src)
        j = ir_to_json(root)
    except Exception as exc:  # noqa: BLE001
        return (False, f"parse/IR failed: {exc.__class__.__name__}: {exc}", {}, set())
    if not isinstance(j, dict):
        return (False, f"IR JSON root not a dict: {type(j).__name__}", {}, set())
    hist: dict[str, int] = {}
    unsup: set[str] = set()
    try:
        tree = sysml2py.Node.from_ir(j)
        dumped = tree.dump()
    except Exception as exc:  # noqa: BLE001
        return (False, f"generated-class build failed: {exc.__class__.__name__}: {exc}", {}, set())
    if not canonical_equals(dumped, src):
        return (False, "canonical mismatch between dumped and source", {}, set())
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
        stack.extend(getattr(node, "children", []) or [])
    return (True, "", hist, unsup)


def main() -> None:
    files = sorted(CORPUS.rglob("*.sysml"))
    passed = 0
    failed = 0
    waivers: list[tuple[str, str]] = []
    histogram: dict[str, int] = {}
    unsupported_for_aliased: set[str] = set()
    for f in files:
        ok, why, hist, unsup = cast_file(f)
        if ok:
            passed += 1
            for cls, n in hist.items():
                histogram[cls] = histogram.get(cls, 0) + n
            unsupported_for_aliased |= unsup
        else:
            failed += 1
            waivers.append((str(f.relative_to(CORPUS)), why))
    print(f"CAST: {passed}/{len(files)} passed, {failed} failed")
    if unsupported_for_aliased:
        print(f"UNSUPPORTED-FOR-ALIASED: {sorted(unsupported_for_aliased)}")
        failed += len(unsupported_for_aliased)
    print(f"INSTANTIATED-CLASSES: {len(histogram)} distinct")
    # Pinned literal, NOT derived from IR_KIND_ALIASES (C1): emptying the
    # alias table must not shrink the expected set until the gate vacates.
    missing = sorted(REQUIRED_ALIASED_CLASSES - set(histogram))
    if missing:
        print(f"MISSING-ALIASED-CLASSES: {missing}")
        failed += len(missing)
    if not REQUIRED_ALIASED_CLASSES <= set(sysml2py.IR_KIND_ALIASES.values()):
        print("IR_KIND_ALIASES shrank below the pinned required set")
        failed += 1
    top = sorted(histogram.items(), key=lambda kv: -kv[1])[:12]
    print("TOP:", ", ".join(f"{k}={v}" for k, v in top))
    if waivers:
        print("WAIVERS:")
        for name, why in waivers:
            print(f"  - {name}: {why}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
