"""read_model round-trip tests (issue: read->edit->emit capability).

Verify the lossless + editable contract of ``sysml2py_lab.read_model``:
  * no-edit:  read_model(f).dump() is canonical-equal to f for EVERY corpus file
  * edit:     read_model(f).add(builder).dump() keeps the original content and
              includes the newly added element (structural round-trip)
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from sysml2py_lab import read_model

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus" / "grammar-2023-07"
CORPUS_FILES = sorted(glob.glob(str(CORPUS_DIR / "*.sysml")))


def _canonical_equal(a: str, b: str) -> bool:
    from sysml2py_lab.regress import canonical_equals

    return canonical_equals(a, b)


@pytest.mark.parametrize("path_s", CORPUS_FILES, ids=lambda p: Path(p).stem)
def test_read_dump_lossless_no_edit(path_s):
    """read -> dump (no edits) must preserve the model (canonical-equal)."""
    src = Path(path_s).read_text(encoding="utf-8")
    model = read_model(path_s)
    assert _canonical_equal(model.dump(), src)


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

        path_s = CORPUS_DIR / "test_Training_Parts_Parts_Example_1.sysml"

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
        assert _canonical_equal(model2.dump(), out)
    finally:
        sys.path.remove(str(pkg / "src"))
        sys.modules.pop("sysml2py", None)


def test_read_add_takes_raw_text_too():
    """add() also accepts a raw SysML string (not just a Builder)."""
    path_s = CORPUS_DIR / "test_Training_Parts_Parts_Example_1.sysml"
    model = read_model(path_s)
    model.add("package Extra { part Z; }")
    assert "package Extra" in model.dump()
