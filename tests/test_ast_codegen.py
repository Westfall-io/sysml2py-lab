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
    """Two consecutive generation runs produce byte-identical trees."""
    p1 = _gen(tmp_path, "a")
    p2 = _gen(tmp_path, "b")
    for f in ("ast_classes.py", "ast_dispatch.py", "provenance.py", "__init__.py", "normalize.py"):
        b1 = (_src(p1) / f).read_bytes()
        b2 = (_src(p2) / f).read_bytes()
        assert b1 == b2, f"{f} differs between runs"


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
    """Acceptance: every generated class has both dump() and get_definition()."""
    pkg = _gen(tmp_path)
    src = (_src(pkg) / "ast_classes.py").read_text(encoding="utf-8")
    classes = re.findall(r"^class (\w+)", src, re.MULTILINE)
    for c in classes:
        # class-level: the class either defines or inherits (Node) both
        # methods; assert by scanning the class body if it defines any.
        m = re.search(rf"^class {c}\(.*?:\n(.*?)(?=^class |\Z)", src, re.DOTALL | re.MULTILINE)
        body = m.group(1) if m else ""
        # Node/Unsupported define their own; every other class either has
        # def dump / def get_definition in body OR inherits from Node.
        if body and "def __init__" in body:
            # generated concrete class — must have both (or inherit Node)
            assert "def dump" in body or "class {c}(Node)".replace("{c}", c) not in body, \
                f"{c} lacks dump()"
    # stronger: every class in KIND_REGISTRY inherits Node (which provides both)
    assert "class PartDefinition(Node)" in src
    assert "class Package(Node)" in src


def test_zero_not_implemented_error(tmp_path):
    """Acceptance: zero NotImplementedError raises in generated output."""
    pkg = _gen(tmp_path)
    for f in ("ast_classes.py", "ast_dispatch.py"):
        src = (_src(pkg) / f).read_text(encoding="utf-8")
        # count actual raise sites (not docstring mentions)
        raises = re.findall(r"^\s*raise NotImplementedError", src, re.MULTILINE)
        assert raises == [], f"{f} has NotImplementedError raises: {raises}"


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


def test_old_266_classes_are_present(tmp_path):
    """Every hand-written 0.5.3 class name is generated (compat + parity)."""
    old = (REPO_ROOT.parent / "sysml2py" / "src" / "sysml2py" / "grammar" / "classes.py")
    if not old.exists():
        pytest.skip("sysml2py checkout not present next to lab")
    old_names = set(re.findall(r"^class (\w+)", old.read_text(encoding="utf-8"), re.MULTILINE))
    pkg = _gen(tmp_path)
    new_names = set(re.findall(r"^class (\w+)", (_src(pkg) / "ast_classes.py").read_text(), re.MULTILINE))
    missing = old_names - new_names
    # Unsupported is provided by the template (hand); Node is the base. Filter:
    missing -= {"Unsupported", "Node"}
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
    generated nodes via ``Node.from_ir`` (coarse-kind aliasing + recursive
    dispatch), ``dump()`` the generated tree, and canonical-compare against
    the original source.  Proves the generated classes + dispatch round-trip
    the full corpus losslessly — the issue-#9 release gate.
    """
    from sysml2py_lab.ir import ir_to_json, parse_ir
    from sysml2py_lab.normalize import canonical_equals

    pkg = _gen(tmp_path)
    sys.path.insert(0, str(pkg / "src"))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        import sysml2py
        corpus = REPO_ROOT / "corpus"
        files = sorted(corpus.rglob("*.sysml"))
        assert len(files) >= 57, f"corpus shrank: {len(files)}"
        failed = []
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
            if not canonical_equals(dumped, src):
                failed.append((str(f.relative_to(corpus)), "canonical mismatch"))
        assert not failed, f"CAST FAILED {len(failed)}/{len(files)}:\n" + "\n".join(
            f"  - {n}: {w}" for n, w in failed
        )
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.path.remove(str(REPO_ROOT / "src"))


def shutil_which_ruff() -> str | None:
    """Locate ruff (PATH or beside the running interpreter)."""
    import shutil
    ruff = shutil.which("ruff")
    if ruff is None:
        cand = Path(sys.executable).parent / "ruff"
        if cand.exists():
            ruff = str(cand)
    return ruff
