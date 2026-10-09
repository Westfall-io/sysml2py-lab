"""Issue #11 — regression harness tests.

Guard the acceptance criteria:
  - replay: every corpus file passes IR + generated-classes canonical compare
    with a per-file fidelity class.
  - roundtrip: text -> generated classes -> get_definition -> dump -> canonical
    AND the semantic definition is lossless (get_definition == IR).
  - determinism: generate twice -> byte-identical.
  - goldens: committed snapshot; drift is detected, refresh is intentional;
    an absent golden is drift.
  - coverage ratchet: FAILS on regression (mutation-proven), passes on progress,
    and treats progress/loss counts with split directions.
  - missing/empty manifest raises (no vacuous green).
  - dump_block_mvp migrated out of the test file into the package.

The generated-package sys.path handling is done by the harness itself
(_ensure_pkg/_drop_pkg), so these tests only pass ``generated_pkg=genroot``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sysml2py_lab import regress as rg
from sysml2py_lab.codegen.emit import EmitOptions, emit_sysml2py
from sysml2py_lab.model import dump_block_mvp  # migrated out of the MVP test
from sysml2py_lab.normalize import normalize_text
from sysml2py_lab.parse_blocks import parse_brace_blocks


@pytest.fixture()
def genroot(tmp_path: Path) -> Path:
    """Generate a package into tmp_path/out and return the package root
    (``emit_sysml2py`` returns the <out>/sysml2py dir)."""
    return emit_sysml2py(tmp_path / "out", opts=EmitOptions())


def test_dump_block_mvp_migrated():
    """AC: dump_block_mvp lives in the package, not the test file."""
    sample = "part sensor {\n  part camera {\n    attribute mass;\n  }\n}"
    root = parse_brace_blocks(sample)
    dumped = dump_block_mvp(root)
    assert normalize_text(dumped) == normalize_text(sample)


def test_regress_replay_full_corpus(genroot):
    """AC: replay runs the whole corpus with per-file pass/fail + fidelity class.
    With a generated package supplied, BOTH IR and AST stages must pass."""
    report = rg.replay(rg.DEFAULT_CORPUS_DIR, generated_pkg=genroot)
    s = report["summary"]
    assert s["files"] >= 62
    assert s["fail"] == 0, f"replay failed {s['fail']} files:\n" + "\n".join(
        f"  {e['file']}: {e.get('errors') or e.get('error')}"
        for e in report["files"] if not e.get("ok")
    )
    assert set(s["fidelity_classes"]) <= {"full", "partial", "opaque", "error"}
    # r2 W2: a fidelity-summary crash must NOT pass silently — gate the error
    # class (it would surface as goldens drift otherwise).
    assert s["fidelity_classes"].get("error", 0) == 0, s["fidelity_classes"]
    for e in report["files"]:
        assert "fidelity_class" in e, e["file"]
        assert e["ir_roundtrip"] is True, f"{e['file']} IR round-trip failed"
        assert e["ast_roundtrip"] is True, f"{e['file']} AST round-trip failed"


def test_regress_replay_gates_on_ast_stage(genroot, tmp_path):
    """r1 B1: replay must FAIL when the AST stage is broken while a package
    was requested — a corpus can't pass on IR alone.

    Uses a SHAM package whose __init__ raises ImportError, so the AST stage
    cannot import and every file must fail.  This genuinely exercises the
    `ok = ir_ok and ast_roundtrip is True` composition (not just the
    missing-path raise, which #_generated_pkg_src handles separately)."""
    sham = tmp_path / "sham"
    pkg = sham / "src" / "sysml2py"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('raise ImportError("sham")', encoding="utf-8")
    report = rg.replay(rg.DEFAULT_CORPUS_DIR, generated_pkg=sham)
    assert report["summary"]["fail"] == report["summary"]["files"] >= 62
    assert report["summary"]["pass"] == 0


def test_regress_roundtrip_full_corpus(genroot):
    """AC: text -> generated classes -> get_definition -> dump -> canonical
    AND get_definition is lossless (equals the IR we lifted from, r1 W1-4)."""
    report = rg.roundtrip(rg.DEFAULT_CORPUS_DIR, generated_pkg=genroot)
    s = report["summary"]
    assert s["fail"] == 0, f"roundtrip failed {s['fail']} files:\n" + "\n".join(
        f"  {e['file']}: {e.get('error')}" for e in report["files"] if not e.get("ok")
    )
    assert s["semantic_pass"] == s["files"], "get_definition() must equal the IR (lossless)"


def test_regress_determinism():
    """AC: generate twice -> byte-identical (the determinism harness path)."""
    report = rg.determinism()
    assert report["ok"], f"determinism failed: {report.get('diffs')} {report.get('error')}"


def test_regress_goldens_check_and_refresh(tmp_path, genroot):
    """AC: goldens detect drift; refresh is intentional and converges."""
    gold_dir = tmp_path / "goldens"
    rg.goldens(rg.DEFAULT_CORPUS_DIR, gold_dir, generated_pkg=genroot, write=True)
    assert (gold_dir / "replay-fidelity.json").exists()
    assert (gold_dir / "coverage-baseline.json").exists()
    # now check (no drift)
    c = rg.goldens(rg.DEFAULT_CORPUS_DIR, gold_dir, generated_pkg=genroot, write=False)
    assert c["ok"], f"goldens drifted: {c['changed']}"


def test_regress_goldens_missing_is_drift(tmp_path, genroot):
    """r1 W1: an absent committed golden reads as drift, not CLEAN."""
    gold_dir = tmp_path / "empty-goldens"
    c = rg.goldens(rg.DEFAULT_CORPUS_DIR, gold_dir, generated_pkg=genroot, write=False)
    assert c["ok"] is False
    assert any("MISSING" in m for m in c["changed"])


def test_regress_manifest_missing_raises(tmp_path):
    """r1 W1: a missing/empty corpus manifest raises (no vacuous green)."""
    empty = tmp_path / "corpus"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        rg.replay(empty)
    with pytest.raises(FileNotFoundError):
        rg.roundtrip(empty)


def test_regress_manifest_empty_raises(tmp_path):
    """r2 W2: a present-but-empty manifest (no declared files) raises."""
    empty = tmp_path / "corpus"
    empty.mkdir()
    (empty / "manifest.json").write_text('{"files": {}}', encoding="utf-8")
    with pytest.raises(ValueError):
        rg.replay(empty)


def test_regress_coverage_ratchet_blocks_regression(tmp_path):
    """AC: the coverage ratchet blocks decreases (mutation-proven).

    A baseline claiming MORE modelled than reality (or MORE progress/loss
    counts than reality) must FAIL.
    """
    base = tmp_path / "base.json"
    base.write_text(json.dumps({
        "coverage": {"modelled": 10_000, "partial": 0, "opaque": 0,
                     "buildable": 0, "ir_roundtrip_files": 0, "files": 0}
    }), encoding="utf-8")
    report = rg.coverage(rg.DEFAULT_CORPUS_DIR, baseline_path=base)
    assert report["ratchet"]["pass"] is False
    assert any("modelled" in r for r in report["ratchet"]["regressions"])
    # r2 W1: loss-direction fail path must ALSO be mutation-proven — the
    # baseline's partial=0 vs reality 146 must trip the partial guard too,
    # else deleting that loop would silently pass.
    assert any("partial" in r for r in report["ratchet"]["regressions"])


def test_regress_coverage_ratchet_passes_at_baseline(tmp_path):
    """AC: the ratchet passes when the real coverage is no worse than baseline.

    Progress counts (modelled/buildable/ir_roundtrip) baseline LOWER than
    reality; loss counts (partial/opaque) baseline HIGHER than reality — both
    make reality the better outcome -> PASS (split directions, r1 B2).
    """
    curr = rg.coverage(rg.DEFAULT_CORPUS_DIR)["coverage"]
    base = tmp_path / "base.json"
    lowered = {
        k: (int(curr[k]) - 10 if k not in ("partial", "opaque") else int(curr[k]) + 10)
        for k in curr
    }
    base.write_text(json.dumps({"coverage": lowered}), encoding="utf-8")
    report = rg.coverage(rg.DEFAULT_CORPUS_DIR, baseline_path=base)
    assert report["ratchet"]["pass"] is True, report["ratchet"]["regressions"]
