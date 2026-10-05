"""Tests for the modifier/prefix model (issue #5)."""

import json
import re
from pathlib import Path

import pytest

from sysml2py_lab.grammar.modifiers import (
    build_modifier_model,
    modifiers_for_kind,
    ModifierError,
)
from sysml2py_lab.grammar.spec import build_spec

INPUTS = Path("grammar_inputs")


@pytest.fixture(scope="module")
def model():
    spec = build_spec(INPUTS)
    return build_modifier_model(spec)


# --- Chain AC: RefPrefix -> BasicUsagePrefix -> OccurrenceUsagePrefix -----

def test_refprefix_chain_matches_classes_py_dump_order(model):
    """RefPrefix must reproduce RefPrefix.dump() order:
    direction -> abstract|variation -> readonly -> derived -> end."""
    rec = modifiers_for_kind(model, "RefPrefix")
    names = [s["name"] for s in rec["slots"]]
    assert names == [
        "direction", "isAbstract", "isVariation",
        "isReadOnly", "isDerived", "isEnd",
    ]


def test_abstract_variation_mutually_exclusive(model):
    """isAbstract ?= 'abstract' | isVariation ?= 'variation'."""
    rec = modifiers_for_kind(model, "RefPrefix")
    by_name = {s["name"]: s for s in rec["slots"]}
    assert by_name["isAbstract"]["mutually_exclusive_with"] == ["isVariation"]
    assert by_name["isVariation"]["mutually_exclusive_with"] == ["isAbstract"]


def test_basicusageprefix_adds_ref_after_refprefix(model):
    """BasicUsagePrefix: RefPrefix + isReference ?= 'ref'."""
    rec = modifiers_for_kind(model, "BasicUsagePrefix")
    names = [s["name"] for s in rec["slots"]]
    # RefPrefix slots first, then isReference last
    assert names[:-1] == [
        "direction", "isAbstract", "isVariation",
        "isReadOnly", "isDerived", "isEnd",
    ]
    assert names[-1] == "isReference"
    assert rec["slots"][-1]["tokens"] == ["ref"]


def test_occurrenceusageprefix_chain(model):
    """OccurrenceUsagePrefix must reproduce OccurrenceUsagePrefix.dump():
    BasicUsagePrefix -> individual -> portionKind."""
    rec = modifiers_for_kind(model, "OccurrenceUsagePrefix")
    names = [s["name"] for s in rec["slots"]]
    assert names == [
        "direction", "isAbstract", "isVariation",
        "isReadOnly", "isDerived", "isEnd",
        "isReference", "isIndividual", "portionKind",
    ]


# --- PortionKind / enum support (today a stub in classes.py) -------------

def test_portionkind_enum_snapshot_timeslice(model):
    """PortionKind: snapshot = 'snapshot' | timeslice = 'timeslice'."""
    rec = modifiers_for_kind(model, "PortionKind")
    by_name = {s["name"]: s for s in rec["slots"]}
    assert set(by_name) == {"snapshot", "timeslice"}
    assert by_name["snapshot"]["tokens"] == ["snapshot"]
    assert by_name["timeslice"]["tokens"] == ["timeslice"]
    assert by_name["snapshot"]["mutually_exclusive_with"] == ["timeslice"]
    assert by_name["timeslice"]["mutually_exclusive_with"] == ["snapshot"]


def test_feature_direction_enum(model):
    """FeatureDirection: in = 'in' | out = 'out' | inout = 'inout'."""
    rec = modifiers_for_kind(model, "FeatureDirection")
    by_name = {s["name"]: s for s in rec["slots"]}
    assert set(by_name) == {"in", "out", "inout"}
    assert by_name["in"]["tokens"] == ["in"]
    # all mutually exclusive
    assert by_name["in"]["mutually_exclusive_with"] == ["out", "inout"]


# --- MemberPrefix ---------------------------------------------------------

def test_memberprefix_visibility(model):
    """MemberPrefix: ( visibility = VisibilityIndicator )?."""
    rec = modifiers_for_kind(model, "MemberPrefix")
    names = [s["name"] for s in rec["slots"]]
    assert names == ["visibility"]


def test_unknown_kind_raises(model):
    with pytest.raises(ModifierError):
        modifiers_for_kind(model, "DoesNotExist_ZZZ")


# --- Whole-model invariants -----------------------------------------------

def test_model_covers_all_known_prefixes(model):
    for name in [
        "RefPrefix", "BasicUsagePrefix", "UsagePrefix",
        "OccurrenceUsagePrefix", "OccurrenceDefinitionPrefix",
        "BasicDefinitionPrefix", "DefinitionPrefix", "MemberPrefix",
    ]:
        assert name in model, f"missing {name}"


def test_all_slots_have_provenance(model):
    """Every slot must carry source_rule + source_line (traceability)."""
    for name, rec in model.items():
        for s in rec["slots"]:
            assert s["source_rule"], f"{name}.{s.get('name')} missing source_rule"
            assert s["source_line"], f"{name}.{s.get('name')} missing source_line"


# --- Corpus spot-check (issue AC: real modifiers recognized) --------------

def test_corpus_modifiers_recognized(model):
    """The modifiers actually used in examples/family.sysml must be
    recognized by the model (>=15 distinct modifier names/tokens)."""
    # collect every slot name + token value (the modifier vocabulary)
    vocab = set()
    for rec in model.values():
        for s in rec["slots"]:
            vocab.add(s["name"])
            vocab.update(s["tokens"])

    # modifiers/prefix-tokens present in family.sysml
    corpus_modifiers = [
        "private",       # private import
        "in", "out",     # action parameters, ports
        "end",           # connection ends, interface ends
        "snapshot",      # Child connection snapshot
        "timeslice",     # Child connection timeslice
        "variation",     # variation part
        "ref",           # ref usage
        "abstract",      # abstract (in grammar, may appear)
        "derived",       # derived
        "readonly",      # readonly
        "individual",    # individual
        "public",        # visibility
        "protected",     # visibility
        "direction",     # slot name for FeatureDirection
        "visibility",    # slot name for MemberPrefix
    ]
    found = [m for m in corpus_modifiers if m in vocab]
    assert len(found) >= 15, f"only {len(found)} corpus modifiers recognized: {found}"

