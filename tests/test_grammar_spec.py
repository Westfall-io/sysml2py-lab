"""Unit tests for the Xtext grammar lexer/parser/spec (issue #3 + review fixes)."""

from pathlib import Path

import pytest

from sysml2py_lab.grammar.lexer import tokenize, LITERAL, IDENT, SYMBOL
from sysml2py_lab.grammar.parser import XtextParser, GrammarParseError
from sysml2py_lab.grammar.spec import (
    build_spec,
    parse_xtext,
    write_spec,
    load_spec,
    SPEC_SCHEMA,
    SPEC_FILENAME,
)


ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_DIR = ROOT / "grammar_inputs"
SPEC_DIR = ROOT / "spec"


# --- lexer --------------------------------------------------------------

def test_lexer_simple():
    toks = tokenize("fragment Identification returns SysML::Element :\n\t'<' declaredShortName = Name '>'\n;")
    vals = [t.value for t in toks]
    assert "fragment" in vals
    assert "Identification" in vals
    # the '<' becomes a literal with unquoted value
    assert any(t.kind == LITERAL and t.value == "<" for t in toks)


def test_lexer_literal_vs_symbol():
    toks = tokenize("';' ;")
    # quoted ';' is a LITERAL, bare ; is a SYMBOL
    assert toks[0].kind == LITERAL and toks[0].value == ";"
    assert toks[1].kind == SYMBOL and toks[1].value == ";"


def test_lexer_range_operator():
    # '..' must be a single symbol (not two '.' symbols)
    toks = tokenize("'0'..'9'")
    assert any(t.kind == SYMBOL and t.value == ".." for t in toks)
    assert not any(t.kind == SYMBOL and t.value == "." and False for t in toks)


def test_lexer_unterminated_comment_raises():
    with pytest.raises(ValueError, match="unterminated block comment"):
        tokenize("Rule: 'a';\n/* oops")


def test_lexer_unterminated_string_raises():
    with pytest.raises(ValueError, match="unterminated string"):
        tokenize("Rule: 'a;")


# --- parser: fixtures ---------------------------------------------------

FIXTURES = [
    # assignments =, +=, ?=
    ("fragment A returns X::Y : foo = Bar isAbstract ?= 'abstract' baz += Qux;",
     lambda b: _find_nodes(b, "assign")),
    # alternation
    ("fragment B returns X::Y : 'a' | 'b' | 'c';",
     lambda b: any(n.get("kind") == "alt" for n in _walk(b))),
    # groups + cardinality
    ("fragment C returns X::Y : ('a' foo = Bar)*;",
     lambda b: any(n.get("kind") == "group" and n.get("card") == "*" for n in _walk(b))),
    # xref
    ("fragment D returns X::Y : client += [X::Element|QualifiedName] (',' client += [X::Element|QualifiedName])*;",
     lambda b: any(n.get("kind") == "xref" and "Element" in n.get("type", "") for n in _walk(b))),
    # action
    ("fragment E returns X::Y : {X::Operator.operand += current} foo = Bar;",
     lambda b: any(n.get("kind") == "action" for n in _walk(b))),
    # syntactic predicates
    ("fragment F returns X::Y : foo => Bar -> Baz;",
     lambda b: any(n.get("kind") == "pred" for n in _walk(b))),
    # terminal keeps body
    ("terminal ID : ('a'..'z' | 'A'..'Z')+;",
     lambda b: b.get("kind") == "terminal_body"),
    # enum keeps body
    ("enum Visibility : public='public' | private='private';",
     lambda b: b.get("kind") == "enum_body"),
    # @Override
    ("@Override OwnedFeature returns X::Y : foo = Bar;",
     lambda b: any(n.get("kind") == "assign" for n in _walk(b))),
]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for x in node:
            yield from _walk(x)


def _find_nodes(node, kind):
    return [n for n in _walk(node) if n.get("kind") == kind]


@pytest.mark.parametrize("src,check", FIXTURES)
def test_fixture_parse(src, check):
    rules, _ = parse_xtext(src, source="fixture.xtext")
    assert len(rules) == 1
    assert rules[0]["source"]["file"] == "fixture.xtext"
    assert check(rules[0]["body"])


def test_fixture_zero_unclassified():
    src = "\n".join(f[0].strip() for f in FIXTURES)
    rules, _ = parse_xtext(src, source="fixture.xtext")
    assert _find_nodes({"body": rules}, "tok") == []


# --- review regression tests (RED) ---------------------------------------

def test_c1_keyword_pipe_not_swallowed():
    """OrOperator `'|'` must emit a lit node, not an empty body."""
    rules, _ = parse_xtext("OrOperator : '|' ;", source="fixture.xtext")
    body = rules[0]["body"]
    assert body["kind"] == "lit" and body["value"] == "|"


def test_c3_predicate_scopes_one_factor():
    """`'{' => Item* '}'` must be 3 siblings, pred body is the single factor."""
    rules, _ = parse_xtext("fragment G returns X::Y : '{' => Item* '}' ;", source="fixture.xtext")
    b = rules[0]["body"]
    assert b["kind"] == "seq" and len(b["items"]) == 3
    pred = b["items"][1]
    assert pred["kind"] == "pred"
    assert pred["body"]["kind"] == "call" and pred["body"]["name"] == "Item"
    assert pred["body"].get("card") == "*"


def test_c4_group_alt_is_nested_alt():
    """`(A | B)` must produce group{body: alt}, not group{items:[A,B]}."""
    rules, _ = parse_xtext("fragment H returns X::Y : (A | B);", source="fixture.xtext")
    b = rules[0]["body"]
    assert b["kind"] == "group"
    assert b["body"]["kind"] == "alt"
    assert len(b["body"]["choices"]) == 2
    # sequence group keeps a single body
    rules2, _ = parse_xtext("fragment I returns X::Y : (A B)*;", source="fixture.xtext")
    g = rules2[0]["body"]
    assert g["kind"] == "group" and g["card"] == "*"
    assert g["body"]["kind"] == "seq"


def test_override_field_recorded():
    rules, _ = parse_xtext("@Override OwnedFeature returns X::Y : foo = Bar;", source="fixture.xtext")
    assert rules[0]["override"] is True


# --- build_spec: keep all rules ------------------------------------------

def test_build_spec_keeps_all_rules(tmp_path):
    a = tmp_path / "a.xtext"
    b = tmp_path / "b.xtext"
    a.write_text("grammar A\nRuleDef returns X::A : foo = Bar;\n")
    b.write_text("grammar B\nRuleDef returns X::B : baz = Qux;\n")
    spec = build_spec(tmp_path)
    names = [r["name"] for r in spec["rules"]]
    # both definitions kept (distinct grammars), not deduped to 1
    assert names.count("RuleDef") == 2
    assert spec["counts"]["total"] == 2
    # each has its file provenance
    files = {r["source"]["file"] for r in spec["rules"]}
    assert files == {"a.xtext", "b.xtext"}
    # file rule_names sum equals rules total
    assert sum(len(f["rule_names"]) for f in spec["files"]) == spec["counts"]["total"]


def test_build_spec_grammar_header(tmp_path):
    g = tmp_path / "g"
    g.mkdir()
    (g / "mini.xtext").write_text(
        "grammar org.omg.sysml.xtext.SysML with org.omg.kerml.expressions.xtext.KerMLExpressions\n"
        "RuleDef returns X::Y : foo = Bar;\n",
        encoding="utf-8",
    )
    spec = build_spec(g)
    gf = spec["files"][0]["grammar"]
    assert gf["name"] == "org.omg.sysml.xtext.SysML"
    assert gf["with"] == ["org.omg.kerml.expressions.xtext.KerMLExpressions"]


# --- whole-grammar smoke + determinism -----------------------------------

def test_whole_grammar_parses_zero_unclassified():
    spec = build_spec(GRAMMAR_DIR)
    assert spec["counts"]["total"] >= 600
    assert all(r["source"]["file"] for r in spec["rules"])
    assert _find_nodes({"rules": spec["rules"]}, "tok") == []


def test_whole_grammar_rule_count_fidelity():
    """Every parsed rule definition must be present (no silent drops)."""
    spec = build_spec(GRAMMAR_DIR)
    assert sum(len(f["rule_names"]) for f in spec["files"]) == spec["counts"]["total"]
    # override flag present on every rule
    assert all("override" in r for r in spec["rules"])


def test_spec_deterministic_byte_identical(tmp_path):
    write_spec(build_spec(GRAMMAR_DIR), tmp_path / "x.json")
    write_spec(build_spec(GRAMMAR_DIR), tmp_path / "y.json")
    assert (tmp_path / "x.json").read_bytes() == (tmp_path / "y.json").read_bytes()


def test_committed_spec_is_current():
    """The committed spec/language_spec.json must equal a fresh build."""
    fresh = build_spec(GRAMMAR_DIR)
    assert fresh == load_spec(SPEC_DIR / SPEC_FILENAME)


def test_spec_round_trip_and_schema(tmp_path):
    s1 = build_spec(GRAMMAR_DIR)
    f = tmp_path / SPEC_FILENAME
    write_spec(s1, f)
    s2 = load_spec(f)
    assert s2["schema"] == SPEC_SCHEMA
    assert s2["counts"] == s1["counts"]
    assert s2["rules"] == s1["rules"]
