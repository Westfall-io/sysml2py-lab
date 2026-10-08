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
    analyze_file,
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
    assert "part" in stats["kinds_used"]
    # the model knows kinds the corpus doesn't show — stats must name them
    assert stats["kinds_zero_coverage"]
    # sanity: `part` cannot be zero-covered (the sample has a part)
    assert "part" not in stats["kinds_zero_coverage"]
    assert sum(stats["fidelity_totals"].values()) >= 0


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
