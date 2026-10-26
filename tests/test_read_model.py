"""read_model round-trip tests (issue: read->edit->emit capability).

Verify the lossless + editable contract of ``sysml2py_lab.read_model``:
  * no-edit:  read_model(f).dump() is canonical-equal to f for EVERY corpus file
  * edit:     read_model(f).add(builder).dump() keeps the original content and
              includes the newly added element (structural round-trip)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sysml2py_lab import read_model, corpus
from sysml2py_lab.normalize import canonical_equals

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
CORPUS_FILES = [str(p) for p in corpus.iter_corpus_files(CORPUS_DIR)]
assert len(CORPUS_FILES) >= 61, (
    f"corpus shrank to {len(CORPUS_FILES)} files; the lossless gate would "
    "silently skip — refusing to run"
)


@pytest.mark.parametrize("path_s", CORPUS_FILES, ids=lambda p: Path(p).stem)
def test_read_dump_lossless_no_edit(path_s):
    """read -> dump (no edits) must preserve the model (canonical-equal)."""
    src = Path(path_s).read_text(encoding="utf-8")
    model = read_model(path_s)
    assert canonical_equals(model.dump(), src)


def test_read_edit_preserves_original_and_adds(tmp_path):
    """read -> add a package -> dump keeps original and includes the new one.

    The generated ``sysml2py`` package is built in-test (repo convention,
    like ``test_ast_codegen``) so this test never depends on a sibling
    checkout and never silently skips.
    """
    import sys

    from sysml2py_lab.codegen.emit import EmitOptions, emit_sysml2py

    pkg = emit_sysml2py(tmp_path / "out", opts=EmitOptions())
    sys.path.insert(0, str(pkg / "src"))
    try:
        import sysml2py

        path_s = CORPUS_DIR / "grammar-2023-07" / "test_Training_Parts_Parts_Example_1.sysml"

        model = read_model(path_s)
        new_pkg = sysml2py.PackageBuilder("NewSubsystem")
        new_pkg.add(sysml2py.Part("Motor").add(
            sysml2py.Attribute("torque", value=10, unit="N*m")))
        model.add(new_pkg)

        out = model.dump()
        # original content survives
        assert "part def Vehicle" in out
        assert "part cyl" in out
        # the added element renders
        assert "package NewSubsystem" in out
        assert "part Motor" in out
        assert "attribute torque = 10[N*m]" in out

        # CASTING: re-parse the edited output — reading the edited file back
        # must not error, and a no-edit round-trip of it must hold.
        out_path = tmp_path / "edited.sysml"
        out_path.write_text(out, encoding="utf-8")
        model2 = read_model(out_path)
        assert canonical_equals(model2.dump(), out)
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.modules.pop("sysml2py", None)


def test_read_add_takes_raw_text_too():
    """add() also accepts a raw SysML string (not just a Builder)."""
    path_s = CORPUS_DIR / "grammar-2023-07" / "test_Training_Parts_Parts_Example_1.sysml"
    model = read_model(path_s)
    model.add("package Extra { part Z; }")
    assert "package Extra" in model.dump()


def test_read_add_comment_cannot_swallow_following_chunk(tmp_path):
    """A `//`-comment chunk must not comment out a later added chunk.

    Regression for the comment-swallowing hazard: `// note` followed by a
    package renders as ``// note\\npackage ...`` (newline-terminated), so the
    package survives re-parsing.  (``canonical_equals`` strips comments, so
    this is asserted structurally, on the re-parsed tree.)
    """
    from sysml2py_lab.ir import parse_ir

    text = "package Base { }\n// note"
    src_path = tmp_path / "base.sysml"
    src_path.write_text(text, encoding="utf-8")
    out = read_model(src_path).add("// note").add("package Extra { part Z; }").dump()
    root = parse_ir(out)
    names = [n.name for n in root.walk() if n.name]
    assert "Extra" in names, f"added package was swallowed by the // comment in:\n{out}"


def test_generated_package_exports_read_model(tmp_path):
    """The user-facing generated ``sysml2py`` package exports read_model.

    ``read_model``/``ReadModel`` are part of the distributable baseline (not a
    factory-only extra): the generated ``sysml2py/__init__`` must re-export
    them, and the read-side modules (lexer/ir/reader) must be present in the
    generated package.
    """
    import sys

    from sysml2py_lab.codegen.emit import EmitOptions, emit_sysml2py

    pkg = emit_sysml2py(tmp_path / "out", opts=EmitOptions())
    sys.path.insert(0, str(pkg / "src"))
    try:
        import sysml2py

        assert hasattr(sysml2py, "read_model")
        assert hasattr(sysml2py, "ReadModel")
        assert (pkg / "src" / "sysml2py" / "lexer.py").exists()
        assert (pkg / "src" / "sysml2py" / "ir.py").exists()
        assert (pkg / "src" / "sysml2py" / "reader.py").exists()
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.modules.pop("sysml2py", None)


def test_generated_read_model_roundtrip_and_edit(tmp_path):
    """End-to-end through the GENERATED package's read_model (user-facing path).

    from sysml2py import read_model, PackageBuilder, Part  — not the lab's
    import.  No-edit dump is canonical-equal to the source; an added package
    survives and the edited output re-parses (casts clean).
    """
    import sys

    from sysml2py_lab.codegen.emit import EmitOptions, emit_sysml2py

    pkg = emit_sysml2py(tmp_path / "out", opts=EmitOptions())
    sys.path.insert(0, str(pkg / "src"))
    try:
        from sysml2py import PackageBuilder, Part, canonical_equals, read_model

        src_text = (
            "package Spacecraft {\n"
            "  part def Vehicle {\n"
            "    part eng : Engine;\n"
            "  }\n"
            "  part primary : Vehicle;\n"
            "}\n"
        )
        src_path = tmp_path / "model.sysml"
        src_path.write_text(src_text, encoding="utf-8")

        # no-edit lossless
        model = read_model(src_path)
        assert canonical_equals(model.dump(), src_text)

        # edit
        model.add(PackageBuilder("NewSubsystem").add(Part("Motor")))
        out = model.dump()
        assert "part def Vehicle" in out
        assert "package NewSubsystem" in out
        assert "part Motor" in out

        # casting: re-read the edited output through the generated read_model
        edited = tmp_path / "edited.sysml"
        edited.write_text(out, encoding="utf-8")
        model2 = read_model(edited)
        assert canonical_equals(model2.dump(), out)
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.modules.pop("sysml2py", None)
