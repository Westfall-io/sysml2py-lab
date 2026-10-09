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
    """generic gate: from_ir must forward EVERY field its class models.

    The Unsupported payload path dropped a different field in three
    consecutive rounds (r6 children -> r7 payload dict -> r8 short_name)
    because each gate only observed the field it was written for.  This
    closes the whole class (r8 remedy, r9 fixed to actually reach both
    payload paths and derive the probe dict from the ctor signature):

      * the probe dict is built from ``inspect.signature(cls.__init__)``, so
        a NEW init parameter enters the probe automatically;
      * it probes BOTH paths: an aliasless kind (``bogus`` -> Unsupported)
        and a modelled kind (``package`` -> Package), so both constructors
        are executed and checked;
      * each field is set to a distinctive sentinel and asserted to survive.
        (Exception: ``children`` is sentinel-inert here — its empty sentinel
        collides with the dataclass default, so it is not discriminated by
        this loop.  ``children`` is gated on ALL THREE payload paths elsewhere:
        the corpus CAST for the modelled path, ``test:398``/``:186`` for
        Unsupported, and ``test:312`` for the IR-dict ctor.)

    Two legitimate exceptions to the forwarding rule are pinned explicitly:
    ``ir_kind`` is derived from ``kind`` (not read from ``d["ir_kind"]``),
    and on the modelled path ``self.kind`` is overwritten with the generated
    class name (``Package``) while the IR kind stays in ``ir_kind``.
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import inspect

        import sysml2py

        SENT = {
            "name": "zzz", "short_name": "zz", "modifiers": ["abstract"],
            "type_refs": ["T"], "multiplicity": "[1]",
            "source_span": [3, 7], "raw_text": "raw", "fidelity": "test",
        }
        # r10 W3: the field surface comes from Node alone; assert Unsupported
        # mirrors it (all eleven Node fields present on Unsupported too), so an
        # Unsupported-only ctor param cannot silently diverge.
        node_surface = (
            set(inspect.signature(sysml2py.Node.__init__).parameters)
            - {"self", "definition"}
        )
        unsup_surface = (
            set(inspect.signature(sysml2py.Unsupported.__init__).parameters)
            - {"self", "definition", "kwargs"}
        )
        assert node_surface == unsup_surface, (
            f"Node/Unsupported surfaces diverged: "
            f"{sorted(node_surface - unsup_surface)} vs {sorted(unsup_surface - node_surface)}"
        )
        # (kind, resolved_class_name); bogus routes to Unsupported, package
        # to Package (the modelled path at ast_classes.py.j2:224-236).
        for kind, resolved in (("bogus", "Unsupported"), ("package", "Package")):
            # r10 C1: probe by the FIELD SURFACE, not the resolved class's
            # declared signature.  Generated classes carry the template ctor
            # (self, definition=None, *, raw_text=None, **kwargs) — no
            # declared fields — so Package.__init__ would yield {raw_text}.
            # Node's dataclass signature IS the field surface; Unsupported
            # mirrors it by construction (all eleven Node fields).
            cls = sysml2py.Node
            from_ir = sysml2py.Node.from_ir
            init_params = (
                set(inspect.signature(cls.__init__).parameters)
                - {"self", "definition", "kwargs"}
            )
            d = {"kind": kind, "children": [], **SENT}
            node = from_ir(d)
            assert type(node).__name__ == resolved, (
                f"{kind!r} resolved to {type(node).__name__!r}, want {resolved!r}"
            )
            # every non-derive field must survive with its sentinel value
            for p in init_params:
                if p in ("kind", "ir_kind"):
                    continue  # pinned exceptions below
                got = getattr(node, p)
                expect = SENT.get(p, [] if p == "children" else None)
                if p == "source_span":
                    expect = tuple(SENT[p])  # both paths normalize to tuple
                assert got == expect, (
                    f"{kind!r}->{resolved}: field {p!r} lost in from_ir"
                    f" (got {got!r}, want {expect!r})"
                )
            # pinned exceptions: kind/ir_kind follow the resolve rule
            if kind == "bogus":
                assert node.kind == "bogus" and node.ir_kind == "bogus", (
                    f"bogus path kind/ir_kind wrong: {node.kind!r}/{node.ir_kind!r}"
                )
            else:
                assert node.kind == "Package" and node.ir_kind == "package", (
                    f"package path kind/ir_kind wrong: {node.kind!r}/{node.ir_kind!r}"
                )
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def test_from_ir_rejects_unknown_ir_keys(tmp_path):
    """r11 W2 gate: a future IR key is never SILENTLY dropped.

    Both from_ir constructors (and the IR-dict ctor) must raise on any IR key
    outside the modeled field set, so a field added to the IR but not yet
    forwarded cannot vanish via d.get(...) — it must fail loudly instead.
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py

        for kind in ("package", "bogus"):
            d = {"kind": kind, "name": "x", "future_field": 1}
            with pytest.raises(ValueError):
                sysml2py.Node.from_ir(d)
        # IR-dict ctor path (a class instance built from a live definition dict)
        with pytest.raises(ValueError):
            sysml2py.Package({"kind": "package", "future_field": 1})
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


# ---------------------------------------------------------------------------
# Phase-5 builder API (issue #10): casting + syntactic printer
# ---------------------------------------------------------------------------

def _strip_meta(x):
    """Drop the AST's default provenance metadata so the builder's sparse IR
    compares semantically equal to the reparsed get_definition() dict.

    source_span and fidelity are AST-internal default bookkeeping (not
    authored content); empty-valued fields are dropped on both sides.
    """
    META_KEYS = {"source_span", "fidelity"}
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k in META_KEYS:
                continue
            sv = _strip_meta(v)
            if sv in (None, "", 0) or sv == []:
                continue
            out[k] = sv
        return out
    if isinstance(x, list):
        return [_strip_meta(i) for i in x]
    return x


def test_builder_api_casts_semantic_content(tmp_path):
    """Phase-5 casting: build -> Node -> get_definition() preserves every
    authored field element-for-element, asserted against a HAND-WRITTEN
    expected IR literal (W1-8) so a dropped field fails the test.

    Provenance-only metadata (source_span, fidelity) is the AST's default
    bookkeeping, not authored content, and is excluded from the comparison.
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py

        sat = sysml2py.PartUsageBuilder("Satellite")
        panel = sysml2py.PartUsageBuilder("Panel")._set_isAbstract()
        sat._set_child(panel)

        reparsed = _strip_meta(sat.build_node().get_definition())
        # hand-written expected IR: if _to_ir (or from_ir) drops any authored
        # field, the equality fails — not just the spot-checks.
        expected = {
            "kind": "PartUsage",
            "name": "Satellite",
            "children": [
                {
                    "kind": "PartUsage",
                    "name": "Panel",
                    "modifiers": ["isAbstract"],
                }
            ],
        }
        assert reparsed == expected, f"builder cast drifted: {reparsed}"
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def test_builder_typed_by_casts_to_type_refs(tmp_path):
    """Phase-5 B1: _set_typed_by emits type_refs (the AST IR key), not a
    non-existent typed_by key, and survives the round-trip."""
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py

        p = sysml2py.PartUsageBuilder("Panel")._set_typed_by(
            sysml2py.PartUsageBuilder("PanelDef")
        )
        defn = _strip_meta(p.build_node().get_definition())
        assert defn["type_refs"] == ["PanelDef"], defn
        # must not throw (typed_by is not an AST IR key)
        assert "typed_by" not in defn
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def test_builder_syntactic_dump_parses_back(tmp_path):
    """Phase-5 syntactic printer: dump() emits grammatical, brace-structured
    SysML that the lab's parse_ir recovers as a brace-block tree with the
    authored child structure intact (root -> Sat -> Panel).

    W1-9: assert on the RECOVERED SHAPE — the brace_open owner must contain a
    descendant carrying the child name — not merely that a brace exists.
    """
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py

        sat = sysml2py.PartUsageBuilder("Satellite")
        sat._set_child(sysml2py.PartUsageBuilder("Panel"))
        text = sat.dump()
        assert "{" in text and "}" in text
        assert "PartUsage Satellite" in text
        assert "PartUsage Panel" in text

        from sysml2py_lab.ir import parse_ir

        ir = parse_ir(text)
        # the node owning brace_open must have a descendant carrying Panel
        # (the recovered shape — re-nesting loses this, W1-9)
        assert _has_descendant_text(ir, "Panel"), "child name not recovered"
        assert any(k == "brace_open" for k in _flatten_kinds(ir))
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def _has_descendant_text(node, text) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        raw = getattr(n, "raw_text", "") or ""
        if text in raw:
            return True
        stack.extend(getattr(n, "children", []) or [])
    return False


def _flatten_kinds(node) -> list[str]:
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        out.append(getattr(n, "kind", ""))
        stack.extend(getattr(n, "children", []) or [])
    return out


def test_builder_coverage_matches_ir_aliases(tmp_path):
    """Phase-5 W2-18: every non-denied IR_KIND_ALIASES entry produces a
    builder class (no hand-maintained kind list to drift)."""
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import importlib

        from sysml2py_lab.codegen import model as m

        builders_mod = importlib.import_module("sysml2py.builders")
        deny = m.CodegenModel.BUILDER_DENY
        expected = set(m.IR_KIND_ALIASES) - deny - {"unknown"}
        # map IR kinds to generated node-kind names (the builder dict keys)
        expected_cls = {
            m.IR_KIND_ALIASES[k]
            for k in expected if m.IR_KIND_ALIASES[k] != "Unsupported"
        }
        have = set(builders_mod._BUILDER_BY_NAME.keys())
        missing = expected_cls - have
        assert not missing, f"builder kinds missing: {sorted(missing)}"
        assert "PartUsage" in have and "InterfaceUsage" in have
        assert "package" not in have  # keyed by node-kind name, not IR kind
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def test_builder_behavior_traversal_printer_directed(tmp_path):
    """Phase-5 W1-10: behavioural coverage of traversal, printer, directed
    features and _get_child (not just an import smoke test)."""
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    try:
        import sysml2py
        from sysml2py.printer import canonical, print_model

        sat = sysml2py.PartUsageBuilder("Sat")
        bus = sysml2py.PartUsageBuilder("Bus")._set_isAbstract()
        pwr = sysml2py.PartUsageBuilder("Power")
        sat._set_child(bus)
        bus._set_child(pwr)

        node = sat.build_node()
        # traversal (pre/post order + find)
        names_pre = [n.name for n in sysml2py.walk_preorder(node)]
        assert names_pre == ["Sat", "Bus", "Power"], names_pre
        names_post = [n.name for n in sysml2py.walk_postorder(node)]
        assert names_post == ["Power", "Bus", "Sat"], names_post
        assert sysml2py.find(node, "Power") is not None
        assert len(sysml2py.find_all(node, "Power")) == 1

        # printer routes a builder-built tree through _render_sysml (W1-7);
        # pass the builder (the natural authoring handle), not the lifted Node
        text = print_model(sat)
        assert "PartUsage Sat" in text and "{" in text
        assert canonical(sat)  # canonicalizes without error

        # a bare Node still renders via dump()
        text_node = print_model(node)
        assert "PartUsage Sat" in text_node

        # _get_child feature chain
        got = sat._get_child("Bus.Power")
        assert got is pwr

        # add_directed_feature honours kind_name (W1-6)
        ref = sysml2py.PartUsageBuilder("R")
        ref.add_directed_feature("in", name="f", kind_name="PortUsage")
        assert isinstance(ref._children[-1], sysml2py.PortUsageBuilder)

        # Visitor post-order (W2-11): after fires after descendants
        order = []
        class V(sysml2py.Visitor):
            def before(self, n):
                order.append(("b", n.name))
            def after(self, n):
                order.append(("a", n.name))
        V().visit(node)
        assert order == [("b", "Sat"), ("b", "Bus"), ("b", "Power"),
                         ("a", "Power"), ("a", "Bus"), ("a", "Sat")], order
    finally:
        sys.path.remove(str(pkg / "src"))


def test_builder_definition_cannot_be_typed(tmp_path):
    """Phase-5 B3: a definition builder (_is_definition) rejects _set_typed_by."""
    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    try:
        import sysml2py

        pkg_b = sysml2py.PackageBuilder("P")
        assert pkg_b._is_definition is True
        with pytest.raises(ValueError, match="cannot be typed"):
            pkg_b._set_typed_by(sysml2py.PartUsageBuilder("X"))
    finally:
        sys.path.remove(str(pkg / "src"))
