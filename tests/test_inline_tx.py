"""Tests for fragment inlining + .tx regeneration (issue #4)."""

import re
from pathlib import Path

import pytest

from sysml2py_lab.grammar.inline import inline_fragments, FragmentError
from sysml2py_lab.grammar.spec import build_spec


ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_DIR = ROOT / "grammar_inputs"
SPEC_DIR = ROOT / "spec"


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


def test_quote_literal_space_and_quote():
    """N-1/N-2: literal quoting must preserve a space literal and escape a
    single-quote literal (no strip, no sniff, always escape)."""
    from sysml2py_lab.grammar.emit_tx import _quote_literal
    # space literal ' ' must NOT become ''
    assert _quote_literal(" ") == "' '"
    # single-quote literal "'" must be escaped, not bare
    assert _quote_literal("'") == "'\\''"
    # backslash literal must be escaped
    assert _quote_literal("\\") == "'\\\\'"
    # ordinary value
    assert _quote_literal("abstract") == "'abstract'"


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


# --- parity / structural-invariants test (the core acceptance criterion) --

def test_emit_tx_structural_invariants():
    """Deterministic-generator contract: the regenerated .tx is a faithful,
    *self-contained* textX grammar.

    Per the deterministic-generator decision (not a clone of the legacy
    hand-curated artifact), we assert structural invariants the generator
    must always satisfy:
      1. no dangling references — every referenced rule name is defined
         either in this file or in an imported grammar;
      2. no raw Xtext constructs — no `=>`/`->` predicates, no `{...}` actions;
      3. every rule ends with ';';
      4. no empty-string or unterminated literals (WS space bug);
      5. the emitted file starts with the correct import headers.
    """
    from sysml2py_lab.grammar.emit_tx import emit_tx_str

    spec = build_spec(GRAMMAR_DIR)
    out = inline_fragments(spec)

    import_files = {
        "KerMLExpressions.xtext": [],
        "KerML.xtext": ["KerMLExpressions.xtext"],
        "SysML.xtext": ["KerMLExpressions.xtext"],
    }

    for src_file, tx_file in [
        ("KerMLExpressions.xtext", "KerMLExpressions.tx"),
        ("KerML.xtext", "KerML.tx"),
        ("SysML.xtext", "SysML.tx"),
    ]:
        text = emit_tx_str(out, file=src_file, original_spec=spec)
        # 5: import headers present
        for imp in import_files[src_file]:
            assert f"import {imp.replace('.xtext', '')}" in text.splitlines()[0:2], \
                f"{tx_file}: missing import {imp}"

        defined = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", text, re.M))
        # union of definitions across the file + its imports (all emitted files)
        all_files_text = "".join(
            emit_tx_str(out, file=f, original_spec=spec)
            for f in import_files[src_file] + [src_file]
        )
        all_defined = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", all_files_text, re.M))

        # 1: dangling references (rule-call / assignment-RHS names).
        # Remove xref targets entirely ([Type|Name]) — those are qualified
        # type references, not rule calls.
        no_xref = re.sub(r"\[[^\]]*\]", " ", text)
        refs = set(re.findall(r"\b([A-Z][A-Za-z0-9_]*)\b(?=\s*(?:\?|\*|\+|\||\)|;|$))", no_xref))
        STANDALONE = {"ID", "INT", "STRING", "BOOLEAN", "DECIMAL", "EXP", "TRUE",
                      "FALSE", "NULL", "WS"}
        dangling = refs - all_defined - STANDALONE
        assert not dangling, f"{tx_file}: dangling refs {sorted(dangling)[:10]}"

        # 2: no raw Xtext predicates.  `=>` never appears in textX; a raw pred
        # `-> Ident` is an Xtext lookahead we must not emit.  Quoted literals
        # ('->') and terminal ranges ('/*' -> '*/') are legitimate and contain
        # -> only inside quotes or before a literal, not before an identifier.
        assert "=>" not in text, f"{tx_file}: raw Xtext pred (=>)"
        assert not re.search(r"->\s+[A-Za-z_]", text), f"{tx_file}: raw Xtext pred (->)"
        assert "{...}" not in text and re.search(r"\{\s*\w", text) is None, f"{tx_file}: raw action"

        # 3: every rule ends with ';'
        n_rules = len(defined)
        assert text.rstrip().endswith(";"), f"{tx_file}: last rule doesn't end ';'"
        # every rule-header line is followed by a body ending in ';'
        rule_ends = re.findall(r"^([A-Za-z_][A-Za-z0-9_]*):.*?;", text, re.M | re.S)
        assert len(rule_ends) >= n_rules - 1, f"{tx_file}: {len(rule_ends)}/{n_rules} rules well-formed"

        # 4: no empty-string literal (N-1) and no unterminated quote (N-2).
        # A literal whose content is empty (`''`) or whose quote never closes
        # is textX-invalid; the escaped-quote idiom `'\''` is fine.  We check
        # the file has balanced quotes and no empty literal tokens.
        assert not re.search(r"(?<!\\)''(?=\s|\)|\|)", text), f"{tx_file}: empty-string literal"
        # balance: every ' opens a literal that closes before a grammar token
        # (approximation: strip the escaped-quote idiom first, then count ')
        stripped = text.replace("\\'", "")
        assert stripped.count("'") % 2 == 0, f"{tx_file}: unbalanced quotes (N-2)"

        print(f"  {tx_file}: {n_rules} rules, {len(dangling)} dangling refs, invariants hold")


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

