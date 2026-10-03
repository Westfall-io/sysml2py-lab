from __future__ import annotations

from pathlib import Path

import pytest

from sysml2py_lab.grammar.inputs import (
    GrammarInputManifest,
    verify_inputs,
    record_manifest,
    write_manifest,
    load_manifest,
    SOURCES_FILENAME,
)


@pytest.fixture
def corpus_input_dir(tmp_path: Path) -> Path:
    """A fake grammar-inputs directory with one .xtext file to verify against."""
    d = tmp_path / "grammar_inputs"
    d.mkdir()
    (d / "mini.xtext").write_text("grammar Mini\nRule: 'a';\n", encoding="utf-8")
    return d


def test_manifest_roundtrip(tmp_path: Path):
    """Writing then loading a manifest must round-trip its entries."""
    d = tmp_path / "g"
    d.mkdir()
    manifest = GrammarInputManifest(entries={"mini.xtext": "abc123"})
    write_manifest(d, manifest)
    loaded = load_manifest(d)
    assert loaded.entries == {"mini.xtext": "abc123"}


def test_record_manifest_records_files(corpus_input_dir: Path):
    """`inputs record` writes a manifest with a sha256 for every file."""
    manifest = record_manifest(corpus_input_dir)
    assert list(manifest.entries.keys()) == ["mini.xtext"]
    assert len(manifest.entries["mini.xtext"]) == 64  # hex sha256


def test_verify_inputs_green_on_unchanged(corpus_input_dir: Path):
    """Verify must pass when the file matches its recorded checksum."""
    recorded = record_manifest(corpus_input_dir)
    verified = verify_inputs(corpus_input_dir)
    assert verified.entries == recorded.entries


def test_verify_inputs_fails_if_no_manifest(corpus_input_dir: Path):
    """Verify must fail (not auto-create) when no manifest exists."""
    with pytest.raises(FileNotFoundError):
        verify_inputs(corpus_input_dir)


def test_verify_inputs_fails_on_drift(corpus_input_dir: Path):
    """Verify must raise when a vendored file's bytes change."""
    record_manifest(corpus_input_dir)
    (corpus_input_dir / "mini.xtext").write_text("grammar Mini\nRule: 'b';\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        verify_inputs(corpus_input_dir)
    assert "drift" in str(exc.value).lower()


def test_verify_inputs_fails_on_unrecorded_new_file(corpus_input_dir: Path):
    """Verify must NOT bless a new file silently (provenance gate)."""
    record_manifest(corpus_input_dir)
    (corpus_input_dir / "new.xtext").write_text("grammar New\nRule: 'c';\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        verify_inputs(corpus_input_dir)
    assert "not recorded" in str(exc.value).lower()


def test_load_manifest_raises_if_missing(tmp_path: Path):
    """Loading a manifest from a dir without one must raise a clear error."""
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path)
