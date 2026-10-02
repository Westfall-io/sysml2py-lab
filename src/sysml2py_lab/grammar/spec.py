"""Build the machine-readable language spec from the Xtext grammar inputs.

Orchestrates: read all vendored .xtext files -> tokenize -> parse each rule
-> assemble a versioned, deterministic `language_spec.json`.  The spec is a
single JSON document:

    {
      "schema": "sysml2py.grammar.language_spec/1",
      "files": [ {"file","line_count","rules"} ],
      "rules": [ {rule dicts, in source order} ],
      "counts": {"rules","fragments","terminals","enums"}
    }

Determinism: JSON keys are emitted sorted, rule lists preserve source
order, and the byte stream is stable for identical inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .lexer import tokenize, IDENT
from .parser import XtextParser, GrammarParseError

SPEC_SCHEMA = "sysml2py.grammar.language_spec/1"
SPEC_FILENAME = "language_spec.json"


@dataclass
class ParsedFile:
    path: str
    line_count: int
    rules: list[dict]


def parse_xtext(text: str, source: str = "") -> list[dict]:
    """Tokenize and parse a full .xtext source into a list of rule dicts.

    Skips the leading 'grammar' declaration line and 'import' lines (they
    are not rules).
    """
    toks = tokenize(text)
    parser = XtextParser(toks, source=source)
    rules: list[dict] = []
    c = parser.c
    while c.pos < c.n:
        # skip leading grammar/import keywords and stray structure
        t = c.peek()
        if t is None:
            break
        if t.value in ("grammar", "import", "with"):
            # consume until we reach a rule-start pattern
            _skip_statement(c)
            continue
        rules.append(parser.parse_rule())
    return rules


def _skip_statement(c):
    """Consume a grammar/import declaration.  In the SysML pilot files these
    are single-line declarations; consume until end-of-line (a NEWLINE token
    would normally be skipped by the lexer, so we stop at the *next* rule
    declaration start heuristically).  We approximate by consuming until a
    rule-start pattern (IDENT ['returns' Type]? ':') appears at top level.
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
    returns clause. We simply check whether a ':' occurs within the next few
    tokens."""
    off = 3
    for _ in range(8):
        t = c.peek(off)
        if t is None:
            return False
        if t.value == ":":
            return True
        if t.value == ";":
            return False
        off += 1
    return False


def parse_file(path: Path) -> ParsedFile:
    text = path.read_text(encoding="utf-8")
    return ParsedFile(
        path=path.name,
        line_count=text.count("\n") + 1,
        rules=parse_xtext(text, source=path.name),
    )


def build_spec(grammar_dir: Path) -> dict:
    """Parse all .xtext under grammar_dir and assemble the spec dict."""
    files: list[dict] = []
    by_name: dict[str, dict] = {}
    for ext in ("*.xtext",):
        for p in sorted(grammar_dir.rglob(ext)):
            pf = parse_file(p)
            for r in pf.rules:
                by_name[r["name"]] = r
            files.append(
                {"file": pf.path, "line_count": pf.line_count,
                 "rule_names": [r["name"] for r in pf.rules]}
            )
    rules = []
    for vidx, (name, r) in enumerate(by_name.items()):
        rr = dict(r)
        rr["index"] = vidx
        rules.append(rr)
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
