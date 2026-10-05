"""Tests for fragment inlining + .tx regeneration (issue #4)."""

import re
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
    """`Frag*` in a host must keep the * on the spliced fragment body.

    C3: the spliced body is wrapped in a group carrying the card, so the
    cardinality is preserved AND precedence is safe.
    """
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
    # Frag* -> group(seq) with the '*' on the wrapper
    assert host["body"]["kind"] == "group"
    assert host["body"]["card"] == "*"
    assert host["body"]["body"]["kind"] == "seq"


def test_emit_group_preserves_cardinality():
    """A group node with a card (?/*/+) must render it — no silent drop.

    (Literals are quoted at emit; the value 'foo' renders as 'foo'.)
    """
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
    assert "('foo')?" in out
    assert "('bar')*" in out


# --- C2 regression: card on alt/lit/assign/xref/pred ---------------------

def test_emit_card_on_alt_and_lit():
    """C2: cards on alt/lit nodes must render, not silently drop."""
    spec = {
        "files": [{"file": "T.xtext"}],
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "T.xtext", "line": 1},
             "body": {"kind": "seq", "items": [
                 {"kind": "alt", "card": "?", "choices": [
                     {"kind": "lit", "value": "a"},
                     {"kind": "lit", "value": "b"},
                 ]},
                 {"kind": "lit", "value": "c", "card": "*"},
             ]}},
        ],
    }
    from sysml2py_lab.grammar.emit_tx import emit_tx_str
    out = emit_tx_str(spec, file="T.xtext")
    assert "('a' | 'b')?" in out
    assert "'c'*" in out


# --- C1 regression: cross-grammar fragment resolution --------------------

def test_fragment_resolution_scoped_per_grammar():
    """C1: fragment with same name in two grammars must resolve per-source-file,
    following the import chain (KerMLExpressions -> KerML -> SysML)."""
    spec = {
        "files": [
            {"file": "KerML.xtext"},
            {"file": "SysML.xtext"},
        ],
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "SysML.xtext", "line": 1},
             "body": {"kind": "call", "name": "Frag", "line": 5}},
            {"name": "Frag", "rule_kind": "fragment", "returns": "X",
             "source": {"file": "KerML.xtext", "line": 3},
             "body": {"kind": "lit", "value": "'kerml'"}},
            {"name": "Frag", "rule_kind": "fragment", "returns": "X",
             "source": {"file": "SysML.xtext", "line": 9},
             "body": {"kind": "lit", "value": "'sysml'"}},
        ],
    }
    out = inline_fragments(spec)
    host = [r for r in out["rules"] if r["name"] == "Host"][0]
    # Host lives in SysML.xtext -> must inline the SysML copy
    assert host["body"]["value"] == "'sysml'", f"got {host['body']['value']}"


# --- C3 regression: alt-rooted fragment splice precedence -----------------

def test_alt_rooted_fragment_splice_wrapped():
    """C3: splicing an alt-rooted fragment into a host seq must wrap in a
    group, so the fragment's | does not fuse with host alternation."""
    spec = {
        "files": [{"file": "T.xtext"}],
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "T.xtext", "line": 1},
             "body": {"kind": "seq", "items": [
                 {"kind": "call", "name": "Frag", "line": 2},
                 {"kind": "lit", "value": ";", "line": 3},
             ]}},
            {"name": "Frag", "rule_kind": "fragment", "returns": "X",
             "source": {"file": "T.xtext", "line": 5},
             "body": {"kind": "alt", "choices": [
                 {"kind": "lit", "value": "a"},
                 {"kind": "lit", "value": "b"},
             ]}},
        ],
    }
    out = inline_fragments(spec)
    host = [r for r in out["rules"] if r["name"] == "Host"][0]
    first = host["body"]["items"][0]
    # the spliced alt must be wrapped in a group node
    assert first["kind"] == "group", f"expected group wrapper, got {first['kind']}"
    assert first["body"]["kind"] == "alt"


# --- C4 regression: literals quoted at emit ------------------------------

def test_emit_literals_quoted():
    """C4: emitted .tx must quote literals (textX requires quotes)."""
    spec = {
        "files": [{"file": "T.xtext"}],
        "rules": [
            {"name": "Host", "rule_kind": "rule", "returns": "X",
             "source": {"file": "T.xtext", "line": 1},
             "body": {"kind": "seq", "items": [
                 {"kind": "lit", "value": "abstract"},
                 {"kind": "lit", "value": "readonly"},
             ]}},
        ],
    }
    from sysml2py_lab.grammar.emit_tx import emit_tx_str
    out = emit_tx_str(spec, file="T.xtext")
    assert "'abstract'" in out
    assert "'readonly'" in out


# --- C6 regression: dict overlay entries applied -------------------------

def test_overlay_dict_entries_applied():
    """C6: dict overlay entries (assignment names) must be applied to the
    emitted .tx, not discarded."""
    from sysml2py_lab.grammar.emit_tx import _load_overlay
    ov = _load_overlay()
    dict_entries = {k: v for k, v in ov.items() if isinstance(v, dict)}
    assert dict_entries, "no dict overlay entries found"
    # a rule whose body references the overlay-keyed fragment directly
    # (PartUsage's body is a call to Usage; overlay maps it to usage=Usage)
    spec = {
        "files": [{"file": "T.xtext"}],
        "rules": [
            {"name": "PartUsage", "rule_kind": "rule", "returns": "X",
             "source": {"file": "T.xtext", "line": 1},
             "body": {"kind": "call", "name": "Usage", "line": 2}},
            {"name": "Usage", "rule_kind": "fragment", "returns": "X",
             "source": {"file": "T.xtext", "line": 5},
             "body": {"kind": "lit", "value": "'part'"}},
        ],
    }
    from sysml2py_lab.grammar.inline import inline_fragments
    from sysml2py_lab.grammar.emit_tx import emit_tx_str
    inlined = inline_fragments(spec)
    out = emit_tx_str(inlined, file="T.xtext", original_spec=spec)
    assert "usage=" in out, f"dict overlay not applied: {out}"


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
    # body_norm: per-rule normalization of the GENERATED body before comparing.
    # Keys are rule names; values are callables applied to the generated body.
    # Used for whitespace-only or documented-normalization divergences where
    # the reference .tx normalizes differently than a faithful regen.
    "body_norm": {
        # KerMLExpressions.tx
        "EXP_VALUE": lambda s: re.sub(r"\s+", "", s),  # whitespace-only normalization
    },
    # ignore_body: rules where the hand-written reference .tx intentionally
    # diverges from a faithful regen of the .xtext (curated richer form).
    # Documented here; body comparison skipped for these.
    "ignore_body": {
        "KerMLExpressions.tx": [
            "Name",     # reference adds !ReservedKeyword guard (not in .xtext)
            "QualifiedName",  # reference hand-wrote a richer names+/['::'] form
            "EXP_VALUE",  # reference layout: trailing ';' on same line (capture artifact)
        ],
    },
}


# --- C5: strengthen the parity test --------------------------------------

def _rule_bodies(text: str) -> dict[str, str]:
    """Extract rule name -> rendered body (strip the trailing ';').

    For a faithful regeneration check, we compare rule *bodies* (with the
    documented whitelist), not just header names.
    """
    import re
    # rule: Name:\n\t<body>\n;
    entries: dict[str, str] = {}
    for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*):\n\t(.*?)\n;", text, re.M | re.S):
        entries[m.group(1)] = m.group(2).strip()
    return entries


def _parity_bodies(new: str, committed: str, whitelist: dict) -> tuple[list[str], list[str]]:
    """Return (missing, diverged) rule names comparing generated vs committed.

    committed_only: names in committed but not regenerated (hand-added rules).
    regen_only: names regenerated but not committed (real rules omitted).
    body_norm: per-rule-name normalization/filter for the body comparison.
    ignore_body: names whose reference body intentionally diverges (curated
    richer form); body comparison skipped for these.
    """
    g = _rule_bodies(new)
    c = _rule_bodies(committed)
    committed_only = set(whitelist.get("committed_only", []))
    regen_only = set(whitelist.get("regen_only", []))
    body_norm = whitelist.get("body_norm", {})
    ignore = set(whitelist.get("ignore_body", []))

    missing = []
    for name in c:
        if name not in g and name not in committed_only:
            missing.append(name)

    diverged = []
    for name in g:
        if name in regen_only or name not in c or name in ignore:
            continue
        cb = c.get(name, "")
        nb = body_norm.get(name, lambda s: s)(g[name])
        if nb != cb:
            diverged.append(name)
    return missing, diverged


def test_emit_tx_reproduces_committed_tx():
    """Re-generated per-file .tx from the spec must reproduce the committed
    .tx modulo a documented whitelist (the issue's AC1).

    The authoritative reference is the hand-curated `sysml2py` grammar .tx
    (the real textX grammar Vesara/Tock parse against).  When that sibling
    checkout is present we compare rule BODIES against it; otherwise we fall
    back to our own committed spec/tx/ artifacts (freshness gate).
    """
    from sysml2py_lab.grammar.emit_tx import emit_tx_str

    spec = build_spec(GRAMMAR_DIR)
    out = inline_fragments(spec)

    sysml2py_grammar = ROOT.parent / "sysml2py" / "src" / "sysml2py" / "grammar"
    reference_dir = sysml2py_grammar if (sysml2py_grammar / "SysML.tx").exists() else (SPEC_DIR / "tx")

    for src_file, tx_file in [
        ("KerMLExpressions.xtext", "KerMLExpressions.tx"),
        ("KerML.xtext", "KerML.tx"),
        ("SysML.xtext", "SysML.tx"),
    ]:
        generated = emit_tx_str(out, file=src_file, original_spec=spec)
        committed = (reference_dir / tx_file).read_text(encoding="utf-8")
        wl = {
            "committed_only": TX_WHITELIST["committed_only"].get(tx_file, []),
            "regen_only": TX_WHITELIST["regen_only"].get(tx_file, []),
            "body_norm": TX_WHITELIST.get("body_norm", {}),
            "ignore_body": TX_WHITELIST.get("ignore_body", {}).get(tx_file, []),
        }
        missing, diverged = _parity_bodies(generated, committed, wl)
        assert not missing, f"{tx_file}: committed rules missing from regen: {missing}"
        assert not diverged, f"{tx_file}: rule bodies diverged: {diverged}"


def test_committed_tx_matches_fresh_regeneration():
    """C5 gate: 'sysml2py-lab tx' output must match the committed spec/tx/*.tx
    — the committed artifacts can't drift from a fresh regeneration."""
    from sysml2py_lab.grammar.emit_tx import emit_tx_str

    spec = build_spec(GRAMMAR_DIR)
    out = inline_fragments(spec)

    for src_file, tx_file in [
        ("KerMLExpressions.xtext", "KerMLExpressions.tx"),
        ("KerML.xtext", "KerML.tx"),
        ("SysML.xtext", "SysML.tx"),
    ]:
        generated = emit_tx_str(out, file=src_file, original_spec=spec)
        committed = (SPEC_DIR / "tx" / tx_file).read_text(encoding="utf-8")
        assert generated == committed, f"{tx_file}: committed artifact is stale (regenerate!)"

