"""Unit tests for fragment inlining + .tx regeneration (issue #4)."""

from pathlib import Path

import pytest

from sysml2py_lab.grammar.inline import inline_fragments, FragmentError
from sysml2py_lab.grammar.spec import build_spec


ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_DIR = ROOT / "grammar_inputs"
SPEC_DIR = ROOT / "spec"


def _rule_headers(text: str) -> list[str]:
    import re
    return re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", text, re.M)


def _parity_ok(new: str, committed: str, whitelist: dict) -> bool:
    """Structural parity: the regenerated .tx is a faithful reproduction of
    the committed .tx modulo the documented normalization whitelist.

    Per the issue's AC1 ("modulo a documented whitelist"):
    1. Every committed rule header must be regenerated, EXCEPT those
       whitelisted as committed-only (hand-added textX conveniences).
    2. Every regenerated rule must be a committed rule, EXCEPT those
       whitelisted as regen-only (real rules the committed .tx omits).
    """
    g = set(_rule_headers(new))
    c = set(_rule_headers(committed))
    committed_only = set(whitelist.get("committed_only", []))
    regen_only = set(whitelist.get("regen_only", []))

    missing = c - g - committed_only   # committed not regenerated (bad, unless whitelisted)
    extra = g - c - regen_only         # regenerated not committed (bad, unless whitelisted)
    return not missing and not extra


# --- inline unit tests (RED) --------------------------------------------

def test_inline_simple_fragment():
    """A host rule referencing a fragment gets the fragment body spliced in."""
    spec = {
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "a.xtext", "line": 1},
             "body": {"kind": "call", "name": "Frag", "line": 5}},
            {"name": "Frag", "rule_kind": "fragment", "returns": "X",
             "source": {"file": "a.xtext", "line": 3},
             "body": {"kind": "lit", "value": "'mod'", "line": 3}},
        ]
    }
    out = inline_fragments(spec)
    host = [r for r in out["rules"] if r["name"] == "Host"][0]
    # the reference is replaced by the fragment body (literal 'mod')
    assert host["body"]["kind"] == "lit"
    assert host["body"]["value"] == "'mod'"
    # fragment itself removed from output rules
    assert not any(r["name"] == "Frag" for r in out["rules"])


def test_inline_regular_rule_call_untouched():
    """A call to a NON-fragment rule is a regular rule reference — inline
    must leave it untouched (fragments are the only things inlined)."""
    spec = {
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "a.xtext", "line": 1},
             "body": {"kind": "call", "name": "Bar", "line": 5}},
            {"name": "Bar", "rule_kind": "rule", "returns": "X",
             "source": {"file": "a.xtext", "line": 3},
             "body": {"kind": "lit", "value": "'x'", "line": 3}},
        ]
    }
    out = inline_fragments(spec)
    host = [r for r in out["rules"] if r["name"] == "Host"][0]
    # regular rule reference preserved as-is
    assert host["body"]["kind"] == "call"
    assert host["body"]["name"] == "Bar"


def test_inline_preserves_cardinality_in_ref_call():
    """`Frag*` in a host must keep the * on the spliced fragment body."""
    spec = {
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "a.xtext", "line": 1},
             "body": {"kind": "call", "name": "Frag", "line": 5, "card": "*"}},
            {"name": "Frag", "rule_kind": "fragment", "returns": "X",
             "source": {"file": "a.xtext", "line": 3},
             "body": {"kind": "seq", "items": [
                 {"kind": "call", "name": "Inner", "line": 3}]}},
        ]
    }
    out = inline_fragments(spec)
    host = [r for r in out["rules"] if r["name"] == "Host"][0]
    # the fragment body is a seq; Frag* means the WHOLE seq repeats
    assert host["body"]["kind"] == "seq"
    assert host["body"].get("card") == "*"


def test_emit_group_preserves_cardinality():
    """A group node with a card (?/*/+) must render it — no silent drop."""
    spec = {
        "files": [{"file": "T.xtext"}],
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "T.xtext", "line": 1},
             "body": {"kind": "seq", "items": [
                 {"kind": "group", "body": {"kind": "lit", "value": "foo"}, "card": "?"},
                 {"kind": "group", "body": {"kind": "lit", "value": "bar"}, "card": "*"},
             ]}},
        ],
    }
    from sysml2py_lab.grammar.emit_tx import emit_tx_str
    out = emit_tx_str(spec, file="T.xtext")
    assert "(foo)?" in out
    assert "(bar)*" in out


# --- token-stream comparison --------------------------------------------

def test_whole_grammar_inline_succeeds():
    """The real 713-rule spec inlines without errors and drops fragments."""
    spec = build_spec(GRAMMAR_DIR)
    out = inline_fragments(spec)
    assert out["counts"]["fragments"] == 0
    assert out["counts"]["total"] > 0
    # no unresolved fragment calls remain
    frag_names = {r["name"] for r in spec["rules"] if r["rule_kind"] == "fragment"}
    assert not frag_names & {r["name"] for r in out["rules"]}


# --- parity test (the core acceptance criterion) -------------------------

#: Documented two-sided whitelist for the parity test.  The committed .tx
#: files are a hand-curated, incomplete rendering of the grammars, so parity
#: with a faithful regen-from-spec is asserted modulo a documented set:
#:   committed_only — names in the committed .tx but NOT regenerated (they
#:     are hand-added textX conveniences with no .xtext source).
#:   regen_only — names regenerated from the .xtext but absent from the
#:     committed .tx (real rules the committed file omitted).
#: Justification per name in spec/schema.md "Parity whitelist".
TX_WHITELIST = {
    # Committed-only, per grammar:
    "committed_only": {
        "KerMLExpressions.tx": [
            "AdditiveOperand", "AndOperand", "Comment", "EqualityOperand",
            "MultiplicativeOperand", "RelationalOperand", "ReservedKeyword",
            "SequenceOperand",
        ],
        "KerML.tx": ["CommentKerML", "MultiplicityRelatedElement"],
        "SysML.tx": [
            "ActionBodyItem", "ActionBodyItemTarget", "CommentSysML",
            "IfNodeElseMember", "ImportPrefix", "ImportedMembership",
            "ImportedNamespace", "MultiplicityRelatedElement",
            "PackageDeclaration", "Redefinitions", "StateDefBody",
        ],
    },
    # Regen-only (real rules the committed .tx omitted), per grammar:
    "regen_only": {
        "KerMLExpressions.tx": [],
        "KerML.tx": [
            "Comment", "FeatureDirection", "FilterPackageMemberVisibility",
            "VisibilityIndicator",
        ],
        "SysML.tx": [
            "AssignmentTargetMember", "Comment", "EffectFeatureKind",
            "EmptyActionUsage", "EmptyParameterMember", "EmptyTargetEnd",
            "EmptyTargetEndMember", "EmptyUsage", "FeatureDirection",
            "FilterPackageMemberVisibility", "FramedConcernKind",
            "GuardFeatureKind", "PortionKind", "RequirementConstraintKind",
            "RequirementVerificationKind", "TargetAccessedFeatureMember",
            "TargetFeature", "TargetFeatureMember", "TargetParameter",
            "TriggerFeatureKind", "VisibilityIndicator",
        ],
    },
}


def test_emit_tx_reproduces_committed_tx():
    """Re-generated per-file .tx from the spec must reproduce the committed
    .tx modulo a documented whitelist (the issue's AC1)."""
    from sysml2py_lab.grammar.emit_tx import emit_tx_str

    spec = build_spec(GRAMMAR_DIR)
    out = inline_fragments(spec)

    # committed .tx files live in the sibling sysml2py repo (read-only ref)
    sysml2py_grammar = ROOT.parent / "sysml2py" / "src" / "sysml2py" / "grammar"
    if not (sysml2py_grammar / "SysML.tx").exists():
        pytest.skip("sibling sysml2py grammar dir not present (read-only ref skips)")

    for src_file, tx_file in [
        ("KerMLExpressions.xtext", "KerMLExpressions.tx"),
        ("KerML.xtext", "KerML.tx"),
        ("SysML.xtext", "SysML.tx"),
    ]:
        generated = emit_tx_str(out, file=src_file)
        committed = (sysml2py_grammar / tx_file).read_text(encoding="utf-8")
        wl = {
            "committed_only": TX_WHITELIST["committed_only"].get(tx_file, []),
            "regen_only": TX_WHITELIST["regen_only"].get(tx_file, []),
        }
        assert _parity_ok(generated, committed, wl), f"parity failed for {tx_file}"
