"""Recursive-descent parser for the Xtext grammar language (sysml2py-lab).

Parses the SysML v2 pilot .xtext files into a structured, ordered element
tree.  It is intentionally resilient: Xtext rules use a rich but regular
sub-grammar, and every token is consumed into a typed node so the whole
grammar parses with zero unclassified tokens.

Node kinds produced (each is a dict with "kind" + kind-specific fields):

  rule         a named grammar rule (kind: rule|fragment|terminal|enum)
       name, returns (optional type string), source {file,line}, body
  seq          ordered sequence of elements  -> {"kind":"seq","items":[..]}
  alt          alternation  -> {"kind":"alt","choices":[..]}
  group        parenthesized group -> {"kind":"group","items":[..]}
  action       {TypeName} -> {"kind":"action","type":"TypeName"}
  assign       name OP ref, OP in = += ?= ?= := -> {"name","op","value":node}
  call         reference to another rule by name -> {"kind":"call","name"}
  lit          keyword literal -> {"kind":"lit","value":"..."}
  xref         [Type|Name] cross-ref -> {"kind":"xref","type","ref"}
  pred         syntactic predicate (=> or ->) -> {"kind":"pred","arrow","body"}
  terminal_body  raw terminal/enum body kept as a token stream

Every element may carry "star"/"plus"/"question" cardinality suffixes and a
"line".  Determinism: the emitting module sorts keys / uses stable orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .lexer import Token, tokenize, IDENT, LITERAL, SYMBOL


class GrammarParseError(Exception):
    pass


@dataclass
class RuleHeader:
    kind: str  # rule | fragment | terminal | enum
    name: str
    returns: str | None
    line: int


# --- token cursor -------------------------------------------------------
class Cursor:
    def __init__(self, toks: list[Token]):
        self.toks = toks
        self.pos = 0
        self.n = len(toks)

    def peek(self, off: int = 0) -> Token | None:
        i = self.pos + off
        return self.toks[i] if 0 <= i < self.n else None

    def at(self, value: str) -> bool:
        t = self.peek()
        return t is not None and t.value == value

    def next(self) -> Token:
        t = self.peek()
        if t is None:
            raise GrammarParseError("unexpected end of tokens")
        self.pos += 1
        return t

    def accept(self, value: str) -> Token | None:
        if self.at(value):
            return self.next()
        return None

    def expect(self, value: str) -> Token:
        t = self.next()
        if t.value != value:
            raise GrammarParseError(f"expected {value!r}, got {t.value!r} at line {t.line}")
        return t

    def accept_ident(self) -> Token | None:
        t = self.peek()
        if t is not None and t.kind == IDENT:
            return self.next()
        return None


def _node(kind: str, line: int, **kw) -> dict:
    d = {"kind": kind, "line": line}
    d.update(kw)
    return d


def _add_cardinality(elt: dict, cur: Cursor):
    t = cur.peek()
    if t is not None and t.kind == SYMBOL and t.value in ("?", "*", "+"):
        cur.next()
        elt["card"] = {"?": "?", "*": "*", "+": "+"}[t.value]
    return elt


class XtextParser:
    def __init__(self, toks: list[Token], source: str = ""):
        self.c = Cursor(toks)
        self.source = source

    # -- rule-level -----------------------------------------------------
    def parse_rule(self):
        c = self.c
        hdr = self._parse_header()
        body = self._parse_body(hdr)
        return {"kind": "rule", "name": hdr.name, "rule_kind": hdr.kind,
                "returns": hdr.returns, "source": {"file": self.source, "line": hdr.line},
                "body": body}

    def _parse_header(self) -> RuleHeader:
        c = self.c
        # optional 'fragment' or 'enum' or 'terminal'/'terminal fragment'
        kind = "rule"
        frag = False
        t = c.peek()
        if t is not None and t.value in ("fragment", "enum", "terminal"):
            kw = c.next().value
            if kw == "terminal":
                # may be 'terminal fragment NAME'
                if c.at("fragment"):
                    c.next()
                    frag = True
                kind = "terminal"
            elif kw == "enum":
                kind = "enum"
            else:  # fragment
                frag = True
                kind = "fragment"
        # @Override may precede the name; tolerates both a combined
        # '@Override' IDENT (this lexer) and '@' + 'Override' tokens.
        while c.peek() is not None and c.peek().value in ("@Override", "@"):
            c.next()
            if c.peek() is not None and c.peek().value == "Override":
                c.next()
        name_tok = c.accept_ident()
        if name_tok is None:
            raise GrammarParseError("expected rule name")
        name = name_tok.value
        returns = None
        if c.at("returns"):
            c.next()
            # type is one or more ident tokens separated by '::'
            parts = []
            while True:
                it = c.accept_ident()
                if it is None:
                    break
                parts.append(it.value)
                if c.at("::"):
                    c.next()
                    continue
                break
            returns = "".join(
                p if i == len(parts) - 1 else p + "::" for i, p in enumerate(parts)
            ) if parts else None
        # optional ':' (rule body) or ';' (empty)
        c.expect(":")
        return RuleHeader(kind, name, returns, name_tok.line)

    def _parse_body(self, hdr: RuleHeader) -> dict:
        c = self.c
        if hdr.kind == "terminal":
            return {"kind": "terminal_body", "tokens": self._collect_until_semicolon()}
        if hdr.kind == "enum":
            return {"kind": "enum_body", "tokens": self._collect_until_semicolon()}
        items = []
        element = self._parse_element()
        if element is not None:
            items.append(element)
            while c.at("|"):
                c.next()
                e2 = self._parse_element()
                if e2 is not None:
                    items.append(e2)
        # consume the rule-terminating ';' if present
        c.accept(";")
        return _clean_seq(items)

    def _collect_until_semicolon(self) -> list[dict]:
        out = []
        while True:
            t = self.c.peek()
            if t is None or t.value == ";":
                break
            self.c.next()
            out.append({"kind": _tok_kind(t), "value": t.value, "line": t.line})
        self.c.accept(";")
        return out

    # -- element ---------------------------------------------------------
    def _parse_element(self) -> dict | None:
        c = self.c
        t = c.peek()
        while t is not None and t.value == "|":
            c.next()
            t = c.peek()
        if t is None:
            return None
        # alternation: collect 'primary' separated by '|'
        first = self._parse_primary()
        if c.at("|"):
            choices = [first]
            while c.at("|"):
                c.next()
                p = self._parse_primary()
                if p is not None:
                    choices.append(p)
            return _add_cardinality(_node("alt", first["line"], choices=choices), c)
        if first is not None and (c.at("|")):
            pass
        return first

    def _parse_primary(self) -> dict | None:
        c = self.c
        t = c.peek()
        if t is None:
            return None
        # skip leading '|'
        while c.at("|"):
            c.next()
        items = []
        while True:
            e = self._parse_factor()
            if e is None:
                break
            items.append(e)
            # stop if we hit a top-level alternative or body end
            if c.at("|") or t_is_end(c):
                break
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        return _node("seq", items[0]["line"], items=items)

    def _parse_factor(self) -> dict | None:
        c = self.c
        t = c.peek()
        if t is None or t_is_end(c) or c.at("|"):
            return None
        line = t.line

        # literal
        if t.kind == LITERAL:
            c.next()
            return _add_cardinality(_node("lit", line, value=t.value), c)

        # parenthesized group
        if c.at("("):
            c.next()
            items = []
            elt = self._parse_primary()
            if elt is not None:
                items.append(elt)
            while c.at("|"):
                c.next()
                e2 = self._parse_primary()
                if e2 is not None:
                    items.append(e2)
            c.expect(")")
            return _add_cardinality(_node("group", line, items=items), c)

        # action {TypeName} or {TypeName.feature = current}
        if c.at("{"):
            c.next()
            # Collect idents, '::', '.', assignment ops up to the closing '}'.
            parts = []
            while True:
                t2 = c.peek()
                if t2 is None or (t2.value == "}" and t2.kind == SYMBOL):
                    break
                c.next()
                parts.append(t2.value)
            c.expect("}")
            return _node("action", line, type=" ".join(parts))

        # assignment: name op value (value may be literal, xref, call, group)
        if t.kind == IDENT and c.peek(1) is not None and c.peek(1).value in ("=", "+=", "?=", ":=", "-="):
            name = t.value
            op = c.peek(1).value
            c.next()  # name
            c.next()  # op
            value = self._parse_assign_value()
            asign = _node("assign", line, name=name, op=op, value=value)
            # cardinality may apply to the whole assignment (e.g. `x ?= 'a'?`)
            return _add_cardinality(asign, c)

        # syntactic predicate => or ->
        if t.value in ("=>", "->"):
            arrow = t.value
            c.next()
            body = self._parse_primary()
            return _node("pred", line, arrow=arrow, body=body)

        # xref [Type|Name]
        if c.at("["):
            c.next()
            parts = []
            while True:
                it = c.accept_ident()
                if it is None:
                    break
                parts.append(it.value)
                if c.at("::"):
                    c.next()
                    continue
                break
            ref = None
            if c.accept("|"):
                ref = self._parse_xref_ref()
            c.expect("]")
            return _add_cardinality(
                _node("xref", line, type="".join(p if i == len(parts) - 1 else p + "::" for i, p in enumerate(parts)),
                      ref=ref), c)

        # rule call (ident) — possibly with an assignment already handled
        if t.kind == IDENT:
            c.next()
            return _add_cardinality(_node("call", line, name=t.value), c)

        # '.' or ',' etc.: treat as literal-ish passthrough token
        c.next()
        return _node("tok", line, value=t.value)

    def _parse_xref_ref(self) -> dict | None:
        c = self.c
        parts = []
        while True:
            it = c.accept_ident()
            if it is None:
                break
            parts.append(it.value)
            if c.at(".") or c.at("::"):
                c.next()
                continue
            break
        return {"name": ".".join(parts)}

    def _parse_assign_value(self) -> dict | None:
        c = self.c
        t = c.peek()
        if t is None:
            return None
        # group value
        if c.at("("):
            c.next()
            items = []
            elt = self._parse_primary()
            if elt is not None:
                items.append(elt)
            while c.at("|"):
                c.next()
                e2 = self._parse_primary()
                if e2 is not None:
                    items.append(e2)
            c.expect(")")
            return _node("group", t.line, items=items)
        # literal
        if t.kind == LITERAL:
            c.next()
            return _node("lit", t.line, value=t.value)
        # xref
        if c.at("["):
            c.next()
            parts = []
            while True:
                it = c.accept_ident()
                if it is None:
                    break
                parts.append(it.value)
                if c.at("::"):
                    c.next()
                    continue
                break
            ref = None
            if c.accept("|"):
                ref = self._parse_xref_ref()
            c.expect("]")
            return _node("xref", t.line, type="::".join(parts), ref=ref)
        # ident call
        if t.kind == IDENT:
            c.next()
            return _node("call", t.line, name=t.value)
        # otherwise passthrough
        c.next()
        return _node("tok", t.line, value=t.value)


    # helper used by tests / hooks
    def expect_ident_or(self, _):
        return self.c.accept_ident()


def _clean_seq(items: list) -> dict:
    if not items:
        return {"kind": "seq", "items": []}
    if len(items) == 1:
        return items[0]
    return {"kind": "seq", "items": items}


def t_is_end(c: Cursor) -> bool:
    t = c.peek()
    if t is None:
        return True
    # a bare ';' or ')' symbol terminates; a quoted literal is a keyword
    return t.kind == SYMBOL and t.value in (";", ")")


def _tok_kind(t: Token) -> str:
    from .lexer import KIND_NAMES
    return KIND_NAMES[t.kind]
