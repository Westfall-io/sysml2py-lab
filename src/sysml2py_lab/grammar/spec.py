"""Build the machine-readable language spec from the Xtext grammar inputs.

Orchestrates: read all vendored .xtext files -> tokenize -> parse each rule
-> assemble a versioned, deterministic `language_spec.json`.  The spec is a
single JSON document:

    {
      "schema": "sysml2py.grammar.language_spec/1",
      "files": [ {"file","line_count","grammar","rules"} ],
      "rules": [ {rule dicts, in stable order} ],
      "counts": {"rules","fragments","terminals","enums"}
    }

Rules are keyed by (file, name): every parsed definition is retained.  A
name defined in more than one file is a *distinct* grammar rule (KerML and
SysML are separate grammars), so nothing is silently dropped.

Determinism: JSON keys are emitted sorted, files are iterated in sorted
path order, rules preserve source order within each file, and the byte
stream is stable for identical inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .lexer import tokenize, IDENT
from .parser import XtextParser, GrammarParseError

SPEC_SCHEMA = "sysml2py.grammar.language_spec/1"
SPEC_FILENAME = "language_spec.json"


@dataclass
class ParsedFile:
    path: str  # repo-relative posix path
    line_count: int
    grammar: dict | None
    rules: list[dict]


def parse_xtext(text: str, source: str = "") -> tuple[list[dict], dict | None]:
    """Tokenize and parse a full .xtext source.

    Returns (rules, grammar).  `grammar` captures the file's grammar
    declaration (name, `with` super-grammar chain, `hidden(...)` terminal
    list, and `import "..." as Alias` URI map).  Rules are every rule /
    fragment / terminal / enum definition in source order.
    """
    toks = tokenize(text)
    parser = XtextParser(toks, source=source)
    rules: list[dict] = []
    c = parser.c
    grammar_decl: dict | None = None
    while c.pos < c.n:
        t = c.peek()
        if t is None:
            break
        if c.at("grammar"):
            grammar_decl = _parse_grammar_decl(c)
            continue
        if c.at("import") or c.at("with"):
            _skip_statement(c)
            continue
        if c.at("hidden"):
            # hidden(WS, ML_COMMENT, ...) - part of the grammar header block
            if grammar_decl is not None:
                grammar_decl["hidden"] = _parse_hidden_decl(c)
            else:
                _skip_statement(c)
            continue
        rules.append(parser.parse_rule())
    return rules, grammar_decl


def _parse_hidden_decl(c) -> list[str]:
    """Parse `hidden(WS, ML_NOTE, SL_NOTE, ...)` -> list of terminal names."""
    c.next()  # 'hidden'
    c.expect("(")
    names = []
    while True:
        it = c.accept_ident()
        if it is not None:
            names.append(it.value)
        if c.at(","):
            c.next()
            continue
        break
    c.expect(")")
    return names


def _parse_grammar_decl(c) -> dict:
    """Parse `grammar <fq-name> with <fq-name> (, <fq-name>)*`."""
    c.next()  # 'grammar'
    name_parts = []
    while True:
        it = c.accept_ident()
        if it is None:
            break
        name_parts.append(it.value)
        if c.at("."):
            c.next()
            continue
        break
    d: dict = {"name": ".".join(name_parts)}
    if c.at("with"):
        c.next()
        supers = []
        while True:
            parts = []
            while True:
                it = c.accept_ident()
                if it is None:
                    break
                parts.append(it.value)
                if c.at("."):
                    c.next()
                    continue
                break
            if parts:
                supers.append(".".join(parts))
            if c.at(","):
                c.next()
                continue
            break
        d["with"] = supers
    return d


def _skip_statement(c):
    """Consume a grammar/import/hidden declaration until a rule-start or ';'.

    Approximate by consuming until a rule-start pattern (IDENT
    ['returns' Type]? ':') appears at top level, or a ';'.
    """
    depth = 0
    while c.pos < c.n:
        t = c.peek()
        if t is None:
            return
        if t.value == ";":
            c.next()
            return
        # rule start heuristic at top level (depth 0)
        if depth == 0:
            n1 = c.peek(1)
            if t.kind == IDENT:
                if n1 is not None and n1.value == ":":
                    return
                if n1 is not None and n1.value == "returns" and _follows_returns(c):
                    return
        if t.value in ("(", "{", "["):
            depth += 1
        elif t.value in (")", "}", "]"):
            depth -= 1
        c.next()


def _follows_returns(c) -> bool:
    """After 'returns <Type>' comes ':'. Look ahead for the ':' that ends the
    returns clause."""
    off = 3
    for _ in range(12):
        t = c.peek(off)
        if t is None:
            return False
        if t.value == ":":
            return True
        if t.value == ";":
            return False
        off += 1
    return False


def parse_file(path: Path, base: Path) -> ParsedFile:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(base).as_posix()
    rules, grammar = parse_xtext(text, source=rel)
    return ParsedFile(
        path=rel,
        line_count=len(text.splitlines()),
        grammar=grammar,
        rules=rules,
    )


def build_spec(grammar_dir: Path) -> dict:
    """Parse all .xtext under grammar_dir and assemble the spec dict."""
    files: list[dict] = []
    rules: list[dict] = []
    for p in sorted(grammar_dir.rglob("*.xtext")):
        pf = parse_file(p, grammar_dir)
        for r in pf.rules:
            rr = dict(r)
            rr["index"] = len(rules)
            rules.append(rr)
        files.append(
            {"file": pf.path, "line_count": pf.line_count,
             "grammar": pf.grammar,
             "rule_names": [r["name"] for r in pf.rules]}
        )
    counts = {
        "rules": sum(1 for r in rules if r["rule_kind"] == "rule"),
        "fragments": sum(1 for r in rules if r["rule_kind"] == "fragment"),
        "terminals": sum(1 for r in rules if r["rule_kind"] == "terminal"),
        "enums": sum(1 for r in rules if r["rule_kind"] == "enum"),
        "total": len(rules),
    }
    return {
        "schema": SPEC_SCHEMA,
        "files": files,
        "rules": rules,
        "counts": counts,
    }


def write_spec(spec: dict, out_path: Path) -> None:
    out_path.write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
