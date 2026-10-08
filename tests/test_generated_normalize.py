from __future__ import annotations

"""Issue #8 — the canonical normalizer is ONE implementation, shipped by
codegen (acceptance criterion).  The generated sysml2py package must contain
the exact same normalize.py bytes as the lab's, so corpus comparison and the
generated library can never diverge."""

import runpy
from pathlib import Path

import pytest

from sysml2py_lab.codegen.emit import emit_sysml2py, EmitOptions
from sysml2py_lab.discover import DiscoveryResult


def test_generated_normalize_is_byte_identical(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.sysml").write_text("package P { part x; }\n", encoding="utf-8")
    res = DiscoveryResult(files_scanned=1, statement_prefix_counts={"package": 1, "part": 1})
    emit_sysml2py(tmp_path / "out", res, opts=EmitOptions(version="0.0.0"))

    lab = (
        Path(__file__).resolve().parents[1]
        / "src" / "sysml2py_lab" / "normalize.py"
    ).read_bytes()
    gen = (tmp_path / "out" / "sysml2py" / "src" / "sysml2py" / "normalize.py").read_bytes()
    assert gen == lab, "generated normalize.py must be byte-identical to the lab's"


def test_generated_normalizer_is_self_contained(tmp_path):
    # the generated normalize must import only stdlib (no sysml2py_lab), so
    # the generated package can run standalone
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.sysml").write_text("package P;", encoding="utf-8")
    res = DiscoveryResult(files_scanned=1, statement_prefix_counts={"package": 1})
    out = tmp_path / "out"
    emit_sysml2py(out, res, opts=EmitOptions(version="0.0.0"))
    gen_py = out / "sysml2py" / "src" / "sysml2py" / "normalize.py"

    src = gen_py.read_text(encoding="utf-8")
    # self-containment is about IMPORTS, not prose: the docstring legitimately
    # references the generated sysml2py package by name.  No `import` line may
    # touch the lab or anything beyond stdlib.
    assert "sysml2py_lab" not in src, "generated normalize must be self-contained"
    for line in src.splitlines():
        l = line.strip()
        if l.startswith("import ") or l.startswith("from "):
            assert l in (
                "import re",
                "from __future__ import annotations",
            ), f"generated normalize must import only stdlib, got: {l!r}"

    # and it actually runs + canonicalizes
    ns = runpy.run_path(str(gen_py))
    assert ns["canonical_tokens"]("part x : T;") == ["part", "x", ":", "T", ";"]
    assert ns["canonical_equals"]("a specializes b;", "a :> b;")
    assert not ns["canonical_equals"]("part x;", "partx;")
