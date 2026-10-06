"""Tests for the children/body-membership model (issue #6).

The model answers the other half of the relationship question: *what may
appear inside each body, and wrapped in which membership node?*  It is
computed mechanically from the grammar, matching how the builders in the
current sysml2py/usage.py hand-assemble membership chains.
"""

import json
import re
from pathlib import Path

import pytest

from sysml2py_lab.grammar.children import (
    build_children_model,
    children_for_body,
    ChildrenError,
)
from sysml2py_lab.grammar.spec import build_spec

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "grammar_inputs"


@pytest.fixture(scope="module")
def model():
    spec = build_spec(INPUTS)
    return build_children_model(spec)


# --- AC1: the builders' hand-assembled membership chains must be
# reproduced by the model ------------------------------------------------

def test_ac1_structure_usage_chain(model):
    """StructureUsageElement -> OccurrenceUsageElement -> OccurrenceUsageMember
    -> DefinitionBodyItem (usage.py usage_dump)."""
    # DefinitionBody may contain an OccurrenceUsageMember wrapping
    # StructureUsageElement via OccurrenceUsageElement
    body = children_for_body(model, "DefinitionBody")
    occ = [e for e in body if e["wrapper"] == "OccurrenceUsageMember"]
    assert occ, "DefinitionBody must allow OccurrenceUsageMember"
    kinds = occ[0]["kinds"]
    assert "PartUsage" in kinds
    assert "ItemUsage" in kinds
    assert "PortUsage" in kinds
    # the resolution chain runs through the capability rules
    assert occ[0]["chain"][0] == "OccurrenceUsageMember"
    assert "OccurrenceUsageElement" in occ[0]["chain"]
    assert "StructureUsageElement" in occ[0]["chain"]


def test_ac1_nonoccurrence_chain(model):
    """NonOccurrenceUsageElement -> NonOccurrenceUsageMember -> DefinitionBodyItem
    (usage.py usage_dump for non-occurrence)."""
    body = children_for_body(model, "DefinitionBody")
    non = [e for e in body if e["wrapper"] == "NonOccurrenceUsageMember"]
    assert non
    assert "AttributeUsage" in non[0]["kinds"]
    assert non[0]["chain"][:2] == ["NonOccurrenceUsageMember", "NonOccurrenceUsageElement"]


# --- Container completeness ----------------------------------------------

def test_all_body_rules_covered(model):
    """Every *Body* rule that is a container must have an entry."""
    for body_name in [
        "DefinitionBody", "RequirementBody", "CaseBody", "StateDefBody",
        "InterfaceBody", "ViewDefinitionBody", "ViewBody", "MetadataBody",
        "NamespaceBody", "PackageBody", "TypeBody", "ActionBody",
        "CalculationBody", "EnumerationBody",
    ]:
        assert body_name in model["bodies"], f"missing container {body_name}"


# --- Per-container allowed children --------------------------------------

def test_definition_body_allows_definition_member(model):
    body = children_for_body(model, "DefinitionBody")
    kinds = [e["wrapper"] for e in body]
    assert "DefinitionMember" in kinds
    assert "VariantUsageMember" in kinds
    assert "AliasMember" in kinds
    assert "Import" in kinds


def test_requirement_body_adds_requirement_members(model):
    body = children_for_body(model, "RequirementBody")
    wrappers = {e["wrapper"] for e in body}
    assert "SubjectMember" in wrappers
    assert "RequirementConstraintMember" in wrappers
    assert "ActorMember" in wrappers
    assert "StakeholderMember" in wrappers
    # requirement body re-includes the definition body items
    assert "DefinitionMember" in wrappers


def test_definition_membership_kinds(model):
    """DefinitionMember wraps DefinitionElement, which includes the 5
    definitions the issue names as missing from classes.py."""
    body = children_for_body(model, "DefinitionBody")
    dm = [e for e in body if e["wrapper"] == "DefinitionMember"][0]
    for kind in [
        "UseCaseDefinition", "OccurrenceDefinition", "ViewDefinition",
        "VerificationCaseDefinition", "MetadataDefinition",
    ]:
        assert kind in dm["kinds"], f"DefinitionMember must allow {kind}"


def test_package_body_members(model):
    body = children_for_body(model, "PackageBody")
    wrappers = {e["wrapper"] for e in body}
    assert "PackageMember" in wrappers
    assert "AliasMember" in wrappers
    assert "ElementFilterMember" in wrappers


# --- ownedRelationship vs ownedRelatedElement -----------------------------

def test_owned_relationship_flag(model):
    """BodyItem wrappers attach to the container via ownedRelationship
    (the body item owns the membership wrapper)."""
    body = children_for_body(model, "DefinitionBody")
    for e in body:
        assert e["relationship"] == "ownedRelationship", e["wrapper"]


# --- Cardinality / ordering ----------------------------------------------

def test_definition_body_item_cardinality(model):
    """DefinitionBodyItem* is zero-or-more; the EmptySuccessionMember prefix
    is optional."""
    body = children_for_body(model, "DefinitionBody")
    # the DefinitionBody: ';' | '{' DefinitionBodyItem* '}' — items repeat
    for e in body:
        assert e.get("cardinality") in ("*", "?", None), e


def test_succession_wrappers_have_ordering(model):
    """OccurrenceUsageMember with EmptySuccessionMember prefix is a
    succession-ordering constraint."""
    body = children_for_body(model, "DefinitionBody")
    occ = [e for e in body if e["wrapper"] == "OccurrenceUsageMember"][0]
    assert "EmptySuccessionMember" in occ.get("prefixes", [])


# --- Parity report --------------------------------------------------------

def test_parity_report_names_missing_definitions(model):
    """The parity report must name the 5 missing definition kinds."""
    report = model.get("parity_report", "")
    assert "UseCaseDefinition" in report
    assert "OccurrenceDefinition" in report
    assert "ViewDefinition" in report
    assert "VerificationCaseDefinition" in report
    assert "MetadataDefinition" in report


def test_known_kinds_resolve(model):
    """children_for_body must resolve every container mentioned in the issue."""
    for b in ["DefinitionBody", "RequirementBody", "EnumerationBody"]:
        items = children_for_body(model, b)
        assert items, f"no children for {b}"


def test_committed_children_fresh():
    """Casting gate: the committed children.json + parity report must equal a
    fresh regeneration (no drift)."""
    from sysml2py_lab.grammar.children import write_children  # noqa: F401  (kept: write path must stay importable)
    import pathlib, subprocess, sys, tempfile, os
    root = pathlib.Path(__file__).resolve().parent.parent
    venv_bin = root / ".venv" / "bin"
    exe = venv_bin / "sysml2py-lab"
    if not exe.exists():
        import pytest
        pytest.skip("no sysml2py-lab console script")
    tmp = tempfile.mkdtemp()
    run = subprocess.run(
        [str(exe), "spec", "children",
         "--out", str(pathlib.Path(tmp) / "children.json"),
         "--out-md", str(pathlib.Path(tmp) / "children.md"),
         "--out-parity", str(pathlib.Path(tmp) / "parity.md")],
        capture_output=True, text=True, cwd=root)
    assert run.returncode == 0, run.stderr
    for name in ["children.json", "parity.md"]:
        fresh = pathlib.Path(tmp) / name
        comm = root / "spec" / ("relationships" if name == "children.json" else "reports") / {
            "children.json": "children.json",
            "parity.md": "classes_parity.md",
        }[name]
        assert fresh.read_text() == comm.read_text(), \
            f"committed {name} drifted from fresh regeneration"
