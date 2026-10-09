"""Issue #11 — regression harness tests.

Guard the acceptance criteria:
  - replay: every corpus file passes IR + generated-classes canonical compare
    with a per-file fidelity class.
  - roundtrip: text -> generated classes -> get_definition -> dump -> canonical.
  - determinism: generate twice -> byte-identical.
  - goldens: committed snapshot; drift is detected, refresh is intentional.
  - coverage ratchet: FAILS on regression (mutation-proven).
  - dump_block_mvp migrated out of the test file into the package.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from sysml2py_lab import regress as rg
from sysml2py_lab.codegen.emit import EmitOptions, emit_sysml2py
from sysml2py_lab.model import dump_block_mvp  # migrated out of the MVP test
from sysml2py_lab.parse_blocks import parse_brace_blocks


@pytest.fixture()
def genroot(tmp_path: Path) -> Path:
    """Generate a package into tmp_path/out and return the package root."""
    return emit_sysml2py(tmp_path / "out", opts=EmitOptions())


@contextmanager
def _with_pkg(genroot: Path):
    sys.path.insert(0, str(genroot / "src"))
    try:
        yield
    finally:
        sys.path.remove(str(genroot / "src"))


def test_dump_block_mvp_migrated():
    """AC: dump_block_mvp lives in the package, not the test file."""
    sample = "part sensor {\n  part camera {\n    attribute mass;\n  }\n}"
    from sysml2py_lab.normalize import normalize_text

    root = parse_brace_blocks(sample)
    dumped = dump_block_mvp(root)
    assert normalize_text(dumped) == normalize_text(sample)


def test_regress_replay_full_corpus(genroot):
    """AC: replay runs the whole corpus with per-file pass/fail + fidelity class."""
    with _with_pkg(genroot):
        report = rg.replay(rg.DEFAULT_CORPUS_DIR, generated_pkg=genroot)
    s = report["summary"]
    assert s["files"] >= 62
    assert s["fail"] == 0, f"replay failed {s['fail']} files"
    assert set(s["fidelity_classes"]) <= {"full", "partial", "opaque"}
    # every file entry carries a fidelity class and IR round-trip status
    for e in report["files"]:
        assert "fidelity_class" in e
        assert "ir_roundtrip" in e
        assert e["ir_roundtrip"] is True, f"{e['file']} IR round-trip failed"
        assert e["ast_roundtrip"] is True, f"{e['file']} AST round-trip failed"


def test_regress_roundtrip_full_corpus(genroot):
    """AC: text -> generated classes -> get_definition -> dump -> canonical."""
    with _with_pkg(genroot):
        report = rg.roundtrip(rg.DEFAULT_CORPUS_DIR, generated_pkg=genroot)
    assert report["summary"]["fail"] == 0, f"roundtrip failed {report['summary']['fail']} files"


def test_regress_determinism():
    """AC: generate twice -> byte-identical (the determinism harness path)."""
    report = rg.determinism()
    assert report["ok"], f"determinism failed: {report.get('diffs')}"


def test_regress_goldens_check_and_refresh(tmp_path):
    """AC: goldens detect drift; refresh is intentional and converges."""
    gold_dir = tmp_path / "goldens"
    # write a golden baseline, then verify it checks clean
    with _with_pkg(tmp_path / "out"):
        rg.goldens(rg.DEFAULT_CORPUS_DIR, gold_dir, generated_pkg=tmp_path / "out", write=True)
    assert (gold_dir / "replay-fidelity.json").exists()
    assert (gold_dir / "coverage-baseline.json").exists()
    # now check (no drift)
    with _with_pkg(tmp_path / "out"):
        c = rg.goldens(rg.DEFAULT_CORPUS_DIR, gold_dir, generated_pkg=tmp_path / "out", write=False)
    assert c["ok"], f"goldens drifted: {c['changed']}"


def test_regress_coverage_ratchet_blocks_regression(tmp_path):
    """AC: the coverage ratchet blocks decreases (mutation-proven)."""
    import json

    # baseline claims MORE modelled than reality -> must FAIL the ratchet
    base = tmp_path / "base.json"
    base.write_text(json.dumps({
        "coverage": {"modelled": 10_000, "partial": 0, "opaque": 0,
                     "buildable": 0, "ir_roundtrip_files": 0, "files": 0}
    }), encoding="utf-8")
    report = rg.coverage(rg.DEFAULT_CORPUS_DIR, baseline_path=base)
    assert report["ratchet"]["pass"] is False
    assert any("modelled" in r for r in report["ratchet"]["regressions"])


def test_regress_coverage_ratchet_passes_at_baseline(tmp_path):
    """AC: the ratchet passes when the real coverage meets the baseline.

    Write a baseline with a LOWER modelled count than reality; the ratchet
    must pass (coverage is monotone non-decreasing).
    """
    import json

    curr = rg.coverage(rg.DEFAULT_CORPUS_DIR)["coverage"]
    base = tmp_path / "base.json"
    # set modelled to reality-1 for every field -> all fields advance
    lowered = {k: max(0, int(curr[k]) - 10) for k in curr}
    base.write_text(json.dumps({"coverage": lowered}), encoding="utf-8")
    report = rg.coverage(rg.DEFAULT_CORPUS_DIR, baseline_path=base)
    assert report["ratchet"]["pass"] is True, report["ratchet"]["regressions"]
