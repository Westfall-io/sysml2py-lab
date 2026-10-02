"""Unit tests for the Xtext grammar lexer/parser/spec (issue #3)."""

import json
import sys
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
    assert "'<'" in vals or "<" in vals
    # all meaningful tokens
    assert all(t.kind != 0 for t in toks)  # no ws tokens


def test_lexer_literal_vs_symbol():
    toks = tokenize("';' ;")
    # quoted ';' is a LITERAL, bare ; is a SYMBOL
    assert toks[0].kind == LITERAL and toks[0].value == ";"
    assert toks[1].kind == SYMBOL and toks[1].value == ";"


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
    rules = parse_xtext(src, source="fixture.xtext")
    assert len(rules) == 1
    assert rules[0]["source"]["file"] == "fixture.xtext"
    assert check(rules[0]["body"])


def test_fixture_zero_unclassified():
    src = "\n".join(f[0].strip() for f in FIXTURES)
    rules = parse_xtext(src, source="fixture.xtext")
    assert _find_nodes({"body": rules}, "tok") == []


def test_fixture_overrides_and_dedup():
    # two files defining the same rule name -> build_spec dedups (later wins)
    a = ROOT / "spec" / "_fixture_a.xtext"
    b = ROOT / "spec" / "_fixture_b.xtext"
    a.write_text("RuleDef returns X::A : foo = Bar;\n")
    b.write_text("RuleDef returns X::B : baz = Qux;\n")
    try:
        spec = build_spec(a.parent)
        names = [r["name"] for r in spec["rules"]]
        assert names.count("RuleDef") == 1
        r = [r for r in spec["rules"] if r["name"] == "RuleDef"][0]
        assert r["returns"] == "X::B"
        assert spec["counts"]["total"] == 1
    finally:
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)


# --- whole-grammar smoke + determinism -----------------------------------

def test_whole_grammar_parses_zero_unclassified():
    spec = build_spec(GRAMMAR_DIR)
    assert spec["counts"]["total"] >= 600
    assert all(r["source"]["file"] for r in spec["rules"])
    # zero raw tok passthroughs across all rule bodies
    assert _find_nodes({"rules": spec["rules"]}, "tok") == []


def test_spec_deterministic_byte_identical(tmp_path):
    s1 = build_spec(GRAMMAR_DIR)
    f1 = tmp_path / "x.json"
    f2 = tmp_path / "y.json"
    write_spec(s1, f1)
    write_spec(s1, f2)
    assert f1.read_bytes() == f2.read_bytes()


def test_spec_round_trip_and_schema(tmp_path):
    s1 = build_spec(GRAMMAR_DIR)
    f = tmp_path / SPEC_FILENAME
    write_spec(s1, f)
    s2 = load_spec(f)
    assert s2["schema"] == SPEC_SCHEMA
    assert s2["counts"] == s1["counts"]
    assert s2["rules"] == s1["rules"]
