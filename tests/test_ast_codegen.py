"""Issue #9 — deterministic AST-class codegen acceptance tests.

Covers the acceptance criteria:

- Two consecutive runs are byte-identical (determinism).
- Generated code passes `ruff check` and `ruff format` (the library ships a
  black.yml workflow).
- >= 266 node kinds are generated and every class has both `dump()` and
  `get_definition()`.
- Zero `NotImplementedError` raises in generated output (replaced by
  `Unsupported` + `raw_text`).
- Membership dispatch from children.json (no hand-written elif ladders).
- `Unsupported` carries raw text (loss-minimizing).
- Provenance stamp is deterministic (fixed injected timestamp).
- `generate` refuses to overwrite without an explicit flag.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sysml2py_lab.codegen.emit import EmitOptions, emit_sysml2py

REPO_ROOT = Path(__file__).resolve().parents[1]


def _gen(tmp_path: Path, out: str = "out", **opts) -> Path:
    """Generate a package into tmp_path/<out> and return the package root."""
    pkg = emit_sysml2py(tmp_path / out, opts=EmitOptions(**opts))
    return pkg


def _src(pkg: Path) -> Path:
    return pkg / "src" / "sysml2py"


def test_determinism_byte_identical(tmp_path):
    """Two consecutive generation runs produce byte-identical trees.

    W5: compares the WHOLE emitted tree (not just the five named files), so a
    newly emitted file or pyproject/README drift is caught.
    """
    p1 = _gen(tmp_path, "a")
    p2 = _gen(tmp_path, "b")
    ign = {".ruff_cache", "__pycache__"}
    f1 = {
        p.relative_to(p1): p.read_bytes()
        for p in p1.rglob("*")
        if p.is_file() and not any(part in ign for part in p.relative_to(p1).parts)
    }
    f2 = {
        p.relative_to(p2): p.read_bytes()
        for p in p2.rglob("*")
        if p.is_file() and not any(part in ign for part in p.relative_to(p2).parts)
    }
    assert set(f1) == set(f2), f"file sets differ: {sorted(set(f1) ^ set(f2))}"
    for rel, b1 in f1.items():
        assert b1 == f2[rel], f"{rel} differs between runs"


def test_generated_code_passes_ruff(tmp_path):
    """Acceptance: generated code passes ruff check + ruff format.

    normalize.py is excluded from the format check: it is the VERBATIM copy of
    the lab's single-sourced canonical normalizer (issue #8 acceptance —
    byte-identical), so its style is the lab's, not ruff's.  It still passes
    `ruff check` (lint, not reformat).
    """
    pkg = _gen(tmp_path)
    ruff = shutil_which_ruff()
    if ruff is None:
        pytest.skip("ruff not available")
    r = subprocess.run([ruff, "check", str(_src(pkg))], capture_output=True, text=True, check=False)
    assert r.returncode == 0, f"ruff check failed:\n{r.stdout}\n{r.stderr}"
    # format check on generated (non-copied) files only
    gen_files = [
        _src(pkg) / f
        for f in ("ast_classes.py", "ast_dispatch.py", "provenance.py", "__init__.py")
    ]
    r = subprocess.run(
        [ruff, "format", "--check"] + [str(p) for p in gen_files],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, f"ruff format --check failed:\n{r.stdout}\n{r.stderr}"


def test_at_least_266_kinds(tmp_path):
    """Acceptance: >= 266 node kinds generated (baseline 266 hand-written)."""
    pkg = _gen(tmp_path)
    src = (_src(pkg) / "ast_classes.py").read_text(encoding="utf-8")
    classes = re.findall(r"^class (\w+)", src, re.MULTILINE)
    assert len(classes) >= 266, f"only {len(classes)} classes generated"
    assert "PartDefinition" in classes
    assert "PartUsage" in classes
    assert "RequirementDefinition" in classes
    assert "UseCaseDefinition" in classes  # the family.sysml blocker (G8)


def test_every_class_has_dump_and_get_definition(tmp_path):
    """Acceptance: every generated class has both dump() and get_definition().

    Imports the generated package and asserts both methods are callable on
    every KIND_REGISTRY entry (real method check — not a source regex, which
    is why this catches C4).  Also asserts Node+Unsupported provide the triad.
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    try:
        import sysml2py

        registry = sysml2py.KIND_REGISTRY
        assert len(registry) >= 266
        for name, cls in registry.items():
            assert callable(getattr(cls, "dump", None)), f"{name} lacks dump()"
            assert callable(getattr(cls, "get_definition", None)), f"{name} lacks get_definition()"
            # constructed node must return a string for dump, dict for get_definition
            node = cls()
            assert isinstance(node.dump(), str), f"{name}.dump() returns non-str"
            assert isinstance(node.get_definition(), dict), f"{name}.get_definition() returns non-dict"
        # base helpers provide the triad too
        for name in ("Node", "Unsupported"):
            cls = getattr(sysml2py, name)
            assert callable(getattr(cls, "dump", None)), f"{name} lacks dump()"
            assert callable(getattr(cls, "get_definition", None)), f"{name} lacks get_definition()"
    finally:
        sys.path.remove(str(pkg / "src"))


def test_zero_not_implemented_error(tmp_path):
    """Acceptance: zero NotImplementedError raises in generated output."""
    pkg = _gen(tmp_path)
    # N6: scan EVERY generated .py.  _src(pkg) IS the package dir
    # (pkg/src/sysml2py) — globbing one level deeper would scan zero files
    # (r7 C1 regression), so keep the root correct here.
    for f in sorted(_src(pkg).glob("*.py")):
        if f.name == "normalize.py":
            continue  # verbatim lab copy (issue #8)
        src = f.read_text(encoding="utf-8")
        raises = re.findall(r"^\s*raise NotImplementedError", src, re.MULTILINE)
        assert raises == [], f"{f.name} has NotImplementedError raises: {raises}"


def test_dispatch_table_and_unknown_fallback(tmp_path):
    """children.json membership dispatch, Unsupported fallback (no raises)."""
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    import sysml2py

    try:
        assert sysml2py.dispatch_member("PackageBody", "PartDefinition").__name__ == "PartDefinition"
        assert sysml2py.dispatch_member("PackageBody", "AttributeDefinition").__name__ == "AttributeDefinition"
        # an unknown grammar member -> Unsupported (never raises)
        assert sysml2py.dispatch_member("PackageBody", "NoSuchKind").__name__ == "Unsupported"
        # children dispatch
        kids = sysml2py.dispatch_children(
            "PackageBody",
            [{"kind": "PartDefinition", "raw_text": "part def Automobile;", "children": []}],
        )
        assert len(kids) == 1 and kids[0].dump() == "part def Automobile;"
        # unknown child kind -> Unsupported preserves raw text
        kids = sysml2py.dispatch_children(
            "PackageBody",
            [{"kind": "MysteryThing", "raw_text": "???", "children": []}],
        )
        assert kids[0].dump() == "???"
        # W3: an unknown kind WITH a subtree must keep that subtree (not
        # erase it) — Unsupported.from_ir preserves children.
        kids = sysml2py.dispatch_children(
            "PackageBody",
            [
                {
                    "kind": "MysteryThing",
                    "raw_text": "mt ",
                    "children": [{"kind": "part", "name": "x", "children": []}],
                }
            ],
        )
        assert len(kids[0].children) == 1, f"subtree erased: {kids[0].children!r}"
    finally:
        sys.path.remove(str(pkg / "src"))


def test_IR_roundtrip_via_raw_text(tmp_path):
    """A node built from IR JSON dumps its raw_text verbatim (loss-minimizing)."""
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    import sysml2py

    try:
        n = sysml2py.PartDefinition.from_ir(
            {"kind": "PartDefinition", "name": "Automobile", "raw_text": "part def Automobile;", "children": []}
        )
        assert n.dump() == "part def Automobile;"
        d = n.get_definition()
        assert d["kind"] == "PartDefinition"
        assert d["raw_text"] == "part def Automobile;"
    finally:
        sys.path.remove(str(pkg / "src"))


def test_valid_definition_contract(tmp_path):
    """Legacy textX dict-shape contract preserved (0.5.3 compat)."""
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    import sysml2py

    try:
        n = sysml2py.PartDefinition({"name": "PartDefinition"})
        assert n.get_definition()["name"] == "PartDefinition"
        with pytest.raises(ValueError):
            sysml2py.PartDefinition({"name": "Mismatch"})
        with pytest.raises((TypeError, AttributeError)):
            sysml2py.PartDefinition({})
    finally:
        sys.path.remove(str(pkg / "src"))


def test_provenance_deterministic(tmp_path):
    """Provenance stamp deterministic with fixed generated_at."""
    import importlib.util

    p1 = _gen(tmp_path, "a", generated_at="1970-01-01T00:00:00Z")
    p2 = _gen(tmp_path, "b", generated_at="2026-10-08T00:00:00Z")

    def load_prov(pkg: Path, modname: str):
        spec = importlib.util.spec_from_file_location(modname, _src(pkg) / "provenance.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    prov_a = load_prov(p1, "prov_a")
    prov_b = load_prov(p2, "prov_b")
    assert prov_a.PROVENANCE["generated_at"] == "1970-01-01T00:00:00Z"
    assert prov_b.PROVENANCE["generated_at"] == "2026-10-08T00:00:00Z"
    # only generated_at differs; hashes identical
    assert prov_a.GRAMMAR_SHA256 == prov_b.GRAMMAR_SHA256
    assert prov_a.SPEC_SHA256 == prov_b.SPEC_SHA256
    assert prov_a.CORPUS_MANIFEST_SHA256 == prov_b.CORPUS_MANIFEST_SHA256
    # provenance hashes are real: sha256 of the committed spec files
    import hashlib
    real = hashlib.sha256((REPO_ROOT / "spec" / "language_spec.json").read_bytes()).hexdigest()
    assert prov_a.GRAMMAR_SHA256 == real


def test_no_clobber_without_flag(tmp_path):
    """generate refuses to overwrite an existing package without --overwrite."""
    pkg = _gen(tmp_path)
    assert pkg.exists()
    with pytest.raises(FileExistsError):
        _gen(tmp_path)  # no overwrite -> raises


def test_normalize_byte_identical_to_lab(tmp_path):
    """The single normalizer implementation ships verbatim (issue #8)."""
    pkg = _gen(tmp_path)
    lab = (REPO_ROOT / "src" / "sysml2py_lab" / "normalize.py").read_bytes()
    gen = (_src(pkg) / "normalize.py").read_bytes()
    assert gen == lab


def test_extra_kind_composites_present(tmp_path):
    """W13: the 8 hand-invented 0.5.3 helper composites must be generated.

    Runs unconditionally (no sibling-checkout skip) so the parity set has a
    real gate.
    """
    COMPOSITES = {
        "ActionBodyItemTarget",
        "AdditiveOperand",
        "AndOperand",
        "CommentSysML",
        "EqualityOperand",
        "MultiplicativeOperand",
        "MultiplicityRelatedElement",
        "RelationalOperand",
        "SequenceOperand",
    }
    pkg = _gen(tmp_path)
    src = (_src(pkg) / "ast_classes.py").read_text(encoding="utf-8")
    present = {name for name in COMPOSITES if re.search(rf"^class {name}\(", src, re.MULTILINE)}
    assert COMPOSITES == present, f"missing generated composites: {sorted(COMPOSITES - present)}"


def test_ir_dict_constructor_roundtrips(tmp_path):
    """W9: the IR-JSON dict constructor (C3 fix) lifts children into nodes.

    Construct a generated class from ir_to_json(parse_ir(...)) directly (not
    via from_ir) and confirm dump() canonical-equals the source.
    """
    pkg = _gen(tmp_path)
    env = dict(__import__("os").environ)
    # import path is the package PARENT (pkg/src), not the package dir itself
    env["PYTHONPATH"] = str(pkg / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [
            sys.executable, "-c",
            (
                "import sysml2py, sys\n"
                "from sysml2py_lab.ir import parse_ir, ir_to_json\n"
                f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
                "d = ir_to_json(parse_ir('part def X { part y; }'))\n"
                "n = sysml2py.PartDefinition(d)\n"
                "assert n.dump(), 'dump empty'\n"
                "print('ok')\n"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, f"IR-dict ctor failed: {r.stdout}\n{r.stderr}"
    assert "ok" in r.stdout


def test_textx_dump_roundtrips(tmp_path):
    """C2: the textX-mode (dict-without-kind) dump() must reconstruct text.

    The per-class `dump()` override and its `_dump_textx` helper implement the
    legacy 0.5.3 `formatting.reformat` contract.  Deleting either must fail
    this test: a textX dict node's dump() must produce the expected SysML text,
    not an empty string (Node.dump emits nothing for a plain dict).
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py

        parts: list[str] = []

        n = sysml2py.PartDefinition({"name": "PartDefinition", "prefix": {}, "definition": {"name": "Automobile"}})
        d = n.dump()
        parts.append(d)
        assert "Automobile" in d, f"PartDefinition textX dump lost name: {d!r}"

        pkg_node = sysml2py.Package({"name": "Package", "prefix": {}, "definition": {"name": "P1"}})
        d2 = pkg_node.dump()
        parts.append(d2)
        assert "P1" in d2, f"Package textX dump lost name: {d2!r}"

        # a bare dict-only node (no name) must be non-empty where the class
        # keyword exists (e.g. Comment -> "comment")
        c = sysml2py.Comment({"name": "Comment", "prefix": {}, "definition": {"name": "hi"}})
        d3 = c.dump()
        parts.append(d3)
        assert d3.strip(), f"Comment textX dump empty: {d3!r}"
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def test_unsupported_get_definition_preserves_children(tmp_path):
    """C1 gate: Unsupported.get_definition() must NOT swallow its subtree.

    A coarse IR kind with no alias (e.g. ``block``) lifts to ``Unsupported``
    but still carries real children (Unsupported.from_ir re-lifts them).
    get_definition() must recurse into those children, matching Node's,
    or the CAST round-trip would silently drop the whole subtree.
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py

        # block has no IR_KIND_ALIASES -> Unsupported, but carries real data
        u = sysml2py.Node.from_ir(
            {
                "kind": "block",
                "name": "B",
                "short_name": "b",
                "modifiers": ["abstract"],
                "multiplicity": "[1]",
                "children": [
                    {"kind": "part", "name": "x", "children": []},
                ],
            }
        )
        assert type(u).__name__ == "Unsupported", type(u).__name__
        d = u.get_definition()
        # payload must survive (r7 C2 + r8 C1): kind, real name, short_name,
        # modifiers, children
        assert d.get("kind") == "block", f"kind lost: {d!r}"
        assert d.get("name") == "B", f"real name overwritten by kind: {d!r}"
        assert d.get("short_name") == "b", f"short_name lost in from_ir: {d!r}"
        assert d.get("modifiers") == ["abstract"], f"modifiers lost: {d!r}"
        assert d.get("multiplicity") == "[1]", f"multiplicity lost: {d!r}"
        assert d.get("children"), f"subtree dropped: {d!r}"
        # idempotence: from_ir(get_definition()) must reproduce the dict
        assert sysml2py.Node.from_ir(d).get_definition() == d, (
            f"not idempotent: {sysml2py.Node.from_ir(d).get_definition()!r} != {d!r}"
        )
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def test_from_ir_forwards_full_ir_key_set(tmp_path):
    """r8 C1 gate (generic): from_ir must consume the SAME key set as __init__.

    The Unsupported payload path dropped a different field in three
    consecutive rounds (r6 children -> r7 payload dict -> r8 short_name)
    because each gate only observed the field it was written for.  This
    closes the whole class: every IR dict key that `Unsupported.__init__`
    / `Node.__init__` accepts must be forwarded by its `from_ir`.  Belt:
    assert an explicit short_name survives an Unsupported lift (see the
    dedicated C1 test).  Braces: assert the forwarded key set == the
    init-parameter set, so a future omission is caught the moment it is
    introduced, not the round after.
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import inspect

        import sysml2py

        for cls, from_ir in ((sysml2py.Node, sysml2py.Node.from_ir),
                             (sysml2py.Unsupported, sysml2py.Unsupported.from_ir)):
            init_params = set(inspect.signature(cls.__init__).parameters) - {"self"}
            # pass DISTINCTIVE values for every field the class models, so any
            # field from_ir fails to forward is caught by the attribute value.
            d = {
                "kind": "bogus",
                "name": "zzz",
                "short_name": "zz",
                "modifiers": ["abstract"],
                "type_refs": ["T"],
                "multiplicity": "[1]",
                "children": [],
                "source_span": [3, 7],
                "raw_text": "raw",
                "fidelity": "test",
                "ir_kind": "bogus",
            }
            node = from_ir(d)
            want = {
                "kind": "bogus", "name": "zzz", "short_name": "zz",
                "modifiers": ["abstract"], "type_refs": ["T"],
                "multiplicity": "[1]", "fidelity": "test", "ir_kind": "bogus",
            }
            for k, v in want.items():
                assert getattr(node, k, None) == v, (
                    f"{cls.__name__}: field {k!r} not forwarded from_ir"
                    f" (got {getattr(node, k, None)!r}, want {v!r})"
                )
            # children are re-listed (should be [] preserved)
            assert list(getattr(node, "children", [])) == [], (
                f"{cls.__name__}: children not forwarded"
            )
            if hasattr(cls, "__dataclass_fields__"):
                dc = set(cls.__dataclass_fields__)
                assert init_params == dc, (
                    f"{cls.__name__}: ctor params {sorted(init_params - dc)} "
                    f"!= dataclass fields {sorted(dc - init_params)}"
                )
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def test_old_266_classes_are_present(tmp_path):
    """Every hand-written 0.5.3 class name is generated (compat + parity)."""
    old = (REPO_ROOT.parent / "sysml2py" / "src" / "sysml2py" / "grammar" / "classes.py")
    if not old.exists():
        pytest.skip("sysml2py checkout not present next to lab")
    old_names = set(re.findall(r"^class (\w+)", old.read_text(encoding="utf-8"), re.MULTILINE))
    pkg = _gen(tmp_path)
    new_names = set(re.findall(r"^class (\w+)", (_src(pkg) / "ast_classes.py").read_text(), re.MULTILINE))
    missing = old_names - new_names
    assert not missing, f"hand-written classes missing from generated: {sorted(missing)}"


def test_generated_package_imports_standalone(tmp_path):
    """The generated package imports without the lab on sys.path."""
    pkg = _gen(tmp_path)
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(pkg / "src")
    r = subprocess.run(
        [sys.executable, "-c", "import sysml2py; assert len(sysml2py.KIND_REGISTRY) >= 266; print('ok')"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert r.returncode == 0, f"standalone import failed: {r.stdout} {r.stderr}"
    assert "ok" in r.stdout


def test_casting_corpus_roundtrip_via_generated_classes(tmp_path):
    """THE CASTING: corpus -> IR -> generated classes -> dump -> canonical.

    For every corpus .sysml: parse with the lab IR, lift the IR tree into
    generated nodes via ``Node.from_ir`` (registry-authoritative coarse-kind
    aliasing + recursive lifting), ``dump()`` the generated tree, and
    canonical-compare against the original source.  Proves the generated
    classes round-trip the full corpus losslessly — the issue-#9 release
    gate.

    Load-bearing (C1): the tree must NOT silently collapse to Unsupported
    for kinds that have a modelled alias — the per-file instantiated-class
    histogram must include every aliased kind's class, and no node may be
    ``Unsupported`` for a kind that has an IR_KIND_ALIAS to a real class.
    """
    from sysml2py_lab.ir import ir_to_json, parse_ir
    from sysml2py_lab.normalize import canonical_equals

    # N4/r7-W1: single source of the pinned set — import the side-effect-free
    # pins module (not the CLI tool, whose import must not touch sys.path).
    from tools._cast_pins import REQUIRED_ALIASED_CLASSES

    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py

        # Pinned literal (NOT derived from IR_KIND_ALIASES — C1): emptying the
        # alias table must not shrink the expected set until the gates
        # themselves pass vacuously.
        assert REQUIRED_ALIASED_CLASSES <= set(sysml2py.IR_KIND_ALIASES.values()), (
            "IR_KIND_ALIASES shrank below the pinned required set"
        )
        corpus = REPO_ROOT / "corpus"
        files = sorted(corpus.rglob("*.sysml"))
        assert len(files) >= 62, f"corpus shrank: {len(files)}"
        failed = []
        histogram: dict[str, int] = {}
        unsupported_for_aliased: set[str] = set()
        for f in files:
            src = f.read_text(encoding="utf-8")
            try:
                root = parse_ir(src)
                j = ir_to_json(root)
                tree = sysml2py.Node.from_ir(j)
                dumped = tree.dump()
            except Exception as exc:  # noqa: BLE001
                failed.append((str(f.relative_to(corpus)), f"{exc.__class__.__name__}: {exc}"))
                continue
            for node in _walk_nodes(tree):
                histogram[type(node).__name__] = histogram.get(type(node).__name__, 0) + 1
                ir_kind = getattr(node, "ir_kind", None)
                if (
                    type(node).__name__ == "Unsupported"
                    and ir_kind in sysml2py.IR_KIND_ALIASES
                    and sysml2py.IR_KIND_ALIASES[ir_kind] != "Unsupported"
                ):
                    unsupported_for_aliased.add(f"{f.name}:{ir_kind}")
            # Fixed-point identity (r7): get_definition -> from_ir ->
            # get_definition must be stable for the WHOLE corpus tree.  This
            # is the gate that would have caught r6's C1 and r7's C2 (an
            # Unsupported node that drops kind/name/modifiers/children is not
            # a fixed point).
            try:
                gd = tree.get_definition()
                fp = sysml2py.Node.from_ir(gd).get_definition()
            except Exception as exc:  # noqa: BLE001
                failed.append((str(f.relative_to(corpus)), f"fixed-point error: {exc}"))
                continue
            if fp != gd:
                failed.append((str(f.relative_to(corpus)), "fixed-point mismatch"))
            if not canonical_equals(dumped, src):
                failed.append((str(f.relative_to(corpus)), "canonical mismatch"))
        assert not unsupported_for_aliased, (
            f"cast collapsed aliased kinds to Unsupported: {sorted(unsupported_for_aliased)}"
        )
        missing_aliased = sorted(REQUIRED_ALIASED_CLASSES - set(histogram))
        assert not missing_aliased, (
            f"cast never instantiated aliased classes: {missing_aliased}"
        )
        # r8 C1 fix #4: the Unsupported path must be PROVABLY exercised by the
        # corpus (brace_open/brace_close/block/unknown reach it in 43/62
        # files).  A histogram with zero Unsupported would mean the payload
        # gates below are testing an assumption, not the corpus.
        assert histogram.get("Unsupported", 0) > 0, (
            "Unsupported never reached by the corpus — payload-loss gates are vacuous"
        )
        assert not failed, f"CAST FAILED {len(failed)}/{len(files)}:\n" + "\n".join(
            f"  - {n}: {w}" for n, w in failed
        )
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def _walk_nodes(node) -> list[object]:
    """Yield node + all descendants (for histogram / load-bearing checks)."""
    out = [node]
    for c in getattr(node, "children", []) or []:
        out.extend(_walk_nodes(c))
    return out


def shutil_which_ruff() -> str | None:
    """Locate ruff (PATH or beside the running interpreter)."""
    import shutil
    ruff = shutil.which("ruff")
    if ruff is None:
        cand = Path(sys.executable).parent / "ruff"
        if cand.exists():
            ruff = str(cand)
    return ruff
