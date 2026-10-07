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


def test_no_duplicate_child_entries(model):
    """C1 regression: KerML + SysML same-name rules must not append
    duplicate entries (MetadataBody/PackageBody had AliasMember+Import twice)."""
    for body_name, entries in model["bodies"].items():
        seen = set()
        for e in entries:
            key = (e["wrapper"], e["cardinality"])
            assert key not in seen, f"duplicate {key} in {body_name}"
            seen.add(key)


def test_no_member_leak_as_kind(model):
    """C2/C3 regression: a member wrapper whose element target resolves must
    not report its own name as the child kind.  Legit leaves (AliasMember,
    Import, Expose, InitialNodeMember) are exempt — these wrappers ARE the
    concrete kind (they carry no element capability)."""
    LEGIT = {"AliasMember", "Import", "Expose", "InitialNodeMember"}
    for body_name, entries in model["bodies"].items():
        for e in entries:
            if e["wrapper"] in LEGIT:
                continue
            assert e["kinds"] != [e["wrapper"]], (
                f"{body_name} {e['wrapper']} leaked wrapper as kind: {e['kinds']}"
            )


def test_transition_members_report_usage_kind(model):
    """C3 regression: TransitionUsageMember/TargetTransitionUsageMember/
    EntryTransitionMember must report the *element* kind (not a sub-member
    like TransitionSourceMember/EmptyParameterMember)."""
    for body_name in ("StateDefBody", "StateUsageBody"):
        for e in children_for_body(model, body_name):
            if e["wrapper"] in ("TransitionUsageMember", "TargetTransitionUsageMember",
                                 "EntryTransitionMember"):
                assert e["kinds"], f"{body_name} {e['wrapper']} empty kinds"
                assert not any(k in e["kinds"] for k in
                               ("TransitionSourceMember", "EmptyParameterMember")), (
                    f"{body_name} {e['wrapper']} leaked sub-member: {e['kinds']}"
                )


def test_cardinality_recorded(model):
    """C4 regression: entries must carry a real card where the grammar
    repeats them (the '*' on the BodyItem call / enclosing group)."""
    db = children_for_body(model, "DefinitionBody")
    assert all(e["cardinality"] == "*" for e in db), [
        (e["wrapper"], e["cardinality"]) for e in db
    ]
    total = sum(len(v) for v in model["bodies"].values())
    stars = sum(1 for v in model["bodies"].values() for e in v if e["cardinality"] == "*")
    assert stars > 0, "no entry recorded a '*' cardinality"
    assert stars < total, "all entries '*' — repetition modeling is meaningless"


def test_function_body_present(model):
    """W1 regression: FunctionBody is a real container and must not be
    silently dropped (FunctionBodyPart holds members directly, no *BodyItem)."""
    entries = children_for_body(model, "FunctionBody")
    assert entries, "FunctionBody dropped"
    wrappers = {e["wrapper"] for e in entries}
    assert {"NonFeatureMember", "FeatureMember", "AliasMember", "Import",
            "ReturnFeatureMember", "ResultExpressionMember"} <= wrappers


def test_no_missing_owned_members(model):
    """C5 regression: every grammar-reachable owned member (transitively,
    following *BodyItem / *BodyPart / *Body delegation) must appear as a
    wrapper or prefix in that body.

    This is the completeness gate the round-2 reviewer asked for (W10):
    `test_function_body_present`-style direct checks miss delegating bodies,
    which contain NO owned assigns of their own (e.g. ActionBody delegates to
    ActionBodyItem).  We walk through the delegation graph.
    """
    import json
    with (ROOT / "spec" / "language_spec.json").open() as specf:
        spec = json.load(specf)
    by = {r["name"]: r for r in spec["rules"]}
    _ALLOWED_UNMODELLED = {"EmptySuccessionMember"}  # a prefix, not a wrapper

    def owned_calls(rule_name: str) -> set[str]:
        """Transitively collect owned-= member call names reachable from a
        rule (through *BodyItem / *BodyPart / *Body delegation)."""
        out: set[str] = set()
        seen: set[str] = set()

        def walk_owned(el, origin: str):
            if not isinstance(el, dict):
                return
            # chase BARE *BodyItem / *BodyPart / *Body delegation calls too
            # (ActionBody: '{' => call(ActionBodyItem) '}' — the BodyItem call
            # is a bare pred-guarded call, NOT an owned-assign value)
            if el.get("kind") == "call" and el.get("name", "").endswith(
                    ("BodyItem", "BodyPart", "Body")):
                tn = el["name"]
                target = by.get(tn)
                if target and tn not in seen:
                    seen.add(tn)
                    walk_owned(target.get("body"), tn)
            if el.get("kind") == "assign" and el.get("name", "").startswith("owned"):
                v = el.get("value")
                if isinstance(v, dict):
                    if v.get("kind") == "call":
                        out.add(v["name"])
                        target = by.get(v["name"])
                        if target and v["name"].endswith(
                                ("BodyItem", "BodyPart", "Body")) and v["name"] not in seen:
                            seen.add(v["name"])
                            walk_owned(target.get("body"), v["name"])
                    elif v.get("kind") == "group" and isinstance(v.get("body"), dict) \
                            and v["body"].get("kind") == "alt":
                        for ch in v["body"].get("choices", []):
                            if ch.get("kind") == "call":
                                out.add(ch["name"])
            for key in ("value", "body"):
                v = el.get(key)
                if isinstance(v, dict):
                    walk_owned(v, origin)
            for key in ("items", "choices"):
                for x in el.get(key, []) or []:
                    walk_owned(x, origin)

        rule = by.get(rule_name)
        if rule:
            walk_owned(rule.get("body"), rule_name)
        return out

    for body_name, entries in model["bodies"].items():
        present = {e["wrapper"] for e in entries} | {
            p for e in entries for p in e["prefixes"]}
        reachable = owned_calls(body_name)
        missing = {n for n in reachable if n not in present
                   and n.endswith(("Member", "BodyPart")) and n not in _ALLOWED_UNMODELLED}
        assert not missing, f"{body_name} missing grammar members: {missing}"


def test_cardinality_audited(model):
    """C6 regression (positive shape, W11): every entry is '*' EXCEPT the
    genuinely-optional ResultExpressionMember in CalculationBody, CaseBody,
    ExpressionBody, FunctionBody (which carry '?').  This catches wrong (not
    just absent) cards — e.g. everything set to '?' would previously pass."""
    OPTIONAL = {
        ("CalculationBody", "ResultExpressionMember"),
        ("CaseBody", "ResultExpressionMember"),
        ("ExpressionBody", "ResultExpressionMember"),
        ("FunctionBody", "ResultExpressionMember"),
    }
    for body_name, entries in model["bodies"].items():
        for e in entries:
            if (body_name, e["wrapper"]) in OPTIONAL:
                assert e["cardinality"] == "?", (
                    f"{body_name} {e['wrapper']} expected '?' got {e['cardinality']}"
                )
            else:
                assert e["cardinality"] == "*", (
                    f"{body_name} {e['wrapper']} expected '*' got {e['cardinality']}"
                )


def test_expression_body_mirrors_calculation(model):
    """C7 regression: ExpressionBody's winning rule is the SysML override
    (CalculationBody alias) — it must expose the same wrappers as
    CalculationBody (NOT the superseded KerMLExpressions default's
    BodyParameterMember).  W12: assert the ABSOLUTE wrapper set too, so
    dropping a member from BOTH bodies fails."""
    calc = {e["wrapper"] for e in children_for_body(model, "CalculationBody")}
    assert calc == {
        "Import", "AliasMember", "DefinitionMember", "VariantUsageMember",
        "NonOccurrenceUsageMember", "StructureUsageMember", "InitialNodeMember",
        "TargetSuccessionMember", "BehaviorUsageMember", "ActionNodeMember",
        "GuardedSuccessionMember", "ReturnParameterMember", "ResultExpressionMember",
    }, f"CalculationBody wrapper set changed: {calc}"
    expr = {e["wrapper"] for e in children_for_body(model, "ExpressionBody")}
    assert calc == expr, (
        f"ExpressionBody != CalculationBody: {expr ^ calc}"
    )
    assert "BodyParameterMember" not in expr


def test_committed_children_fresh():
    """Casting gate: committed children.json + children.md + parity report must
    equal fresh regeneration (no drift).  The generator is invoked via the
    venv's sysml2py-lab console script; if that is missing the gate FAILS
    (never silently skips — a green no-op would defeat the casting gate)."""
    import pathlib, subprocess, tempfile
    root = pathlib.Path(__file__).resolve().parent.parent
    venv_bin = root / ".venv" / "bin"
    exe = venv_bin / "sysml2py-lab"
    if not exe.exists():
        pytest.fail(f"sysml2py-lab console script missing at {exe} — casting gate cannot run")
    tmp = tempfile.mkdtemp()
    run = subprocess.run(
        [str(exe), "spec", "children",
         "--out", str(pathlib.Path(tmp) / "children.json"),
         "--out-md", str(pathlib.Path(tmp) / "children.md"),
         "--out-parity", str(pathlib.Path(tmp) / "parity.md")],
        capture_output=True, text=True, cwd=root)
    assert run.returncode == 0, run.stderr
    for name in ["children.json", "children.md", "parity.md"]:
        fresh = pathlib.Path(tmp) / name
        subdir = "relationships" if name != "parity.md" else "reports"
        comm = root / "spec" / subdir / {
            "children.json": "children.json",
            "children.md": "children.md",
            "parity.md": "classes_parity.md",
        }[name]
        assert fresh.read_text() == comm.read_text(), \
            f"committed {name} drifted from fresh regeneration"
