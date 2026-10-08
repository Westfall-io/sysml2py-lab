from __future__ import annotations

"""Issue #8 — corpus intake pipeline (add / verify / stats) + manifest.

Guards the acceptance criteria:
  - `corpus add` copies + records provenance/sha/fidelity/kinds
  - `corpus verify` fails on sha mismatch or unparseable file
  - `corpus stats` reports node-kind coverage AND names kinds with ZERO
    coverage against the relationship model (children.json)
  - every corpus file parses to IR with fidelity recorded
"""

import hashlib
import json

from pathlib import Path

import pytest

from sysml2py_lab.corpus import (
    add_file,
    corpus_stats,
    iter_corpus_files,
    load_manifest,
    verify_corpus,
)
from sysml2py_lab.discover import discover_corpus

_SAMPLE = "package P {\n    part x;\n    attribute a : Real;\n}\n"


def _mk_corpus(tmp_path: Path) -> Path:
    c = tmp_path / "corpus"
    c.mkdir()
    return c


def test_add_records_manifest(tmp_path):
    c = _mk_corpus(tmp_path)
    src = tmp_path / "s.sysml"
    src.write_text(_SAMPLE, encoding="utf-8")
    rel = add_file(c, src, source="test", source_url="http://x", license="MIT", dest_subdir="t")
    assert rel == "t/s.sysml"
    m = load_manifest(c)
    e = m["files"]["t/s.sysml"]
    assert e["sha256"] == hashlib.sha256(_SAMPLE.encode()).hexdigest()
    assert e["source"] == "test"
    assert e["windtrader"] == "unverified"
    assert e["fidelity"]["modelled"] >= 2  # package + part + attribute
    assert "part" in e["node_kinds"]
    assert "attribute" in e["node_kinds"]


def test_verify_ok_and_sha_mismatch(tmp_path):
    c = _mk_corpus(tmp_path)
    src = tmp_path / "s.sysml"
    src.write_text(_SAMPLE, encoding="utf-8")
    add_file(c, src, source="test", dest_subdir="t")
    rep = verify_corpus(c)
    assert rep["errors"] == []
    assert rep["checked"] == 1
    # corrupt the file -> verify must fail
    (c / "t" / "s.sysml").write_text(_SAMPLE + "// tamper\n", encoding="utf-8")
    rep2 = verify_corpus(c)
    assert any("sha256 mismatch" in e for e in rep2["errors"])


def test_verify_missing_file(tmp_path):
    c = _mk_corpus(tmp_path)
    src = tmp_path / "s.sysml"
    src.write_text(_SAMPLE, encoding="utf-8")
    add_file(c, src, source="test", dest_subdir="t")
    (c / "t" / "s.sysml").unlink()
    rep = verify_corpus(c)
    assert any("missing file" in e for e in rep["errors"])


def test_stats_reports_zero_coverage_kinds(tmp_path):
    c = _mk_corpus(tmp_path)
    src = tmp_path / "s.sysml"
    src.write_text(_SAMPLE, encoding="utf-8")
    add_file(c, src, source="test", dest_subdir="t")
    stats = corpus_stats(c)
    assert stats["files"] == 1
    assert stats["model_loaded"] is True  # default model path resolves
    assert "part" in stats["files_with_kind"]
    # the model knows kinds the corpus doesn't show — stats must name them
    assert stats["kinds_zero_coverage"]
    # sanity: `part` cannot be zero-covered (the sample has a part)
    assert "part" not in stats["kinds_zero_coverage"]
    # `unknown` (opaque bucket) must not be reported as a covered kind
    assert "unknown" not in stats["files_with_kind"]
    assert sum(stats["fidelity_totals"].values()) >= 0


def test_stats_distinguishes_no_model(tmp_path):
    """Missing relationship model must NOT report perfect coverage (W3)."""
    c = _mk_corpus(tmp_path)
    src = tmp_path / "s.sysml"
    src.write_text(_SAMPLE, encoding="utf-8")
    add_file(c, src, source="test", dest_subdir="t")
    stats = corpus_stats(c, model_path="/nonexistent/children.json")
    assert stats["model_loaded"] is False
    assert stats["kinds_zero_coverage"] == []


def test_iter_corpus_files_deterministic(tmp_path):
    c = _mk_corpus(tmp_path)
    for i, name in enumerate(["b.sysml", "a.sysml", "c.sysml"]):
        (c / name).write_text(f"package P{i};", encoding="utf-8")
    files = [p.name for p in iter_corpus_files(c)]
    assert files == sorted(files)


def test_discover_has_node_kind_counts(tmp_path):
    c = _mk_corpus(tmp_path)
    src = tmp_path / "s.sysml"
    src.write_text(_SAMPLE, encoding="utf-8")
    add_file(c, src, source="test", dest_subdir="t")
    res = discover_corpus(c)
    assert res.files_scanned == 1
    # the brace-block prefix histogram counts Line children; `package P {`
    # is a Block header, so only the inner lines appear as prefixes
    assert res.statement_prefix_counts.get("part") == 1
    assert res.statement_prefix_counts.get("attribute") == 1
    # relationship-aware kinds come from the IR and DO include the package
    assert res.node_kind_counts.get("package") == 1
    assert res.node_kind_counts.get("part") == 1
    assert res.node_kind_counts.get("attribute") == 1


def test_add_is_deterministic(tmp_path):
    """Re-adding the same file yields an identical manifest entry."""
    c = _mk_corpus(tmp_path)
    src = tmp_path / "s.sysml"
    src.write_text(_SAMPLE, encoding="utf-8")
    add_file(c, src, source="test", dest_subdir="t")
    m1 = json.dumps(load_manifest(c), sort_keys=True)
    add_file(c, src, source="test", dest_subdir="t")
    m2 = json.dumps(load_manifest(c), sort_keys=True)
    assert m1 == m2


# ---------------------------------------------------------------------------
# Tests over the REAL committed corpus/ tree (issue #8, review round 1 C4).
# These would have caught the mining defects: Python source leaked into a
# fixture, closing triple-quotes leaked in, and unbalanced braces.  They
# also pin the load-bearing render_ir //-termination fix (mutation M6).
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus"


def test_real_corpus_verifies_clean():
    """Every committed corpus file passes sha256 integrity + parses to IR."""
    pytest.importorskip("sysml2py_lab")
    from sysml2py_lab.corpus import verify_corpus, iter_corpus_files
    n = len(list(iter_corpus_files(CORPUS_DIR)))
    assert n >= 57, f"corpus must hold >=57 files, found {n}"
    rep = verify_corpus(CORPUS_DIR)
    assert rep["errors"] == [], f"corpus verify failed: {rep['errors']}"
    assert rep["checked"] == n


def test_real_corpus_no_python_leak():
    """No commented fixture may contain Python harness source."""
    for p in CORPUS_DIR.rglob("*.sysml"):
        if "__commented" in p.name:
            txt = p.read_text(encoding="utf-8")
            assert "classtree" not in txt and "loads(text)" not in txt \
                and "strip_ws" not in txt, f"Python source leaked into {p.name}: {txt!r}"


def test_real_corpus_no_leaked_quotes_and_balanced_braces():
    """No fixture may contain a stray triple-quote; braces must balance."""
    from sysml2py_lab.normalize import canonical_tokens
    for p in CORPUS_DIR.rglob("*.sysml"):
        src = p.read_text(encoding="utf-8")
        o, c = src.count("{"), src.count("}")
        assert o == c, f"unbalanced braces in {p.name} ({{={o} }}={c})"
        toks = canonical_tokens(src)
        assert toks.count("{") == toks.count("}"), f"token brace imbalance in {p.name}"


def test_real_corpus_roundtrips_canonically():
    """The core acceptance criterion: every corpus file round-trips to IR
    under canonical normalization (src == render_ir(parse_ir(src)))."""
    from sysml2py_lab.ir import parse_ir, render_ir
    from sysml2py_lab.normalize import canonical_equals
    failures = []
    for p in CORPUS_DIR.rglob("*.sysml"):
        src = p.read_text(encoding="utf-8")
        if not canonical_equals(render_ir(parse_ir(src)), src):
            failures.append(p.relative_to(CORPUS_DIR).as_posix())
    assert failures == [], f"round-trip failed for: {failures}"


def test_family_corpus_in_sync_with_examples():
    """corpus/family/family.sysml must stay byte-identical to
    examples/family.sysml (review W6) so provenance isn't drifted."""
    ex = REPO_ROOT / "examples" / "family.sysml"
    cp = CORPUS_DIR / "family" / "family.sysml"
    assert ex.exists(), "examples/family.sysml missing"
    if cp.exists():
        assert cp.read_bytes() == ex.read_bytes(), \
            "corpus/family/family.sysml drifted from examples/family.sysml"
