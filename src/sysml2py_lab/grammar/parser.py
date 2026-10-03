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
  group        parenthesized group -> {"kind":"group","body":node}
  action       {TypeName} -> {"kind":"action","type":"TypeName"}
  assign       name OP ref, OP in = += ?= := -> {"name","op","value":node}
  call         reference to another rule by name -> {"kind":"call","name"}
  lit          keyword literal -> {"kind":"lit","value":"..."}
  xref         [Type|Name] cross-ref -> {"kind":"xref","type","ref"}
  pred         syntactic predicate (=> or ->) -> {"kind":"pred","arrow","body"}
  terminal_body  raw terminal/enum body kept as a token stream

Every element may carry a "card" cardinality ("?","*","+") and a "line".
Determinism: the emitting module sorts keys / uses stable orders.

Structural tokens are matched *kind-aware*: a quoted `';'` keyword literal
must never be confused with the bare `;` rule terminator, `'|'` with the
alternation separator, or `')'` with a group close.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import Token, IDENT, LITERAL, SYMBOL


class GrammarParseError(Exception):
    pass


@dataclass
class RuleHeader:
    kind: str  # rule | fragment | terminal | enum
    name: str
    returns: str | None
    line: int
    override: bool = False


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

    def at_sym(self, value: str) -> bool:
        t = self.peek()
        return t is not None and t.kind == SYMBOL and t.value == value

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

    def accept_sym(self, value: str) -> Token | None:
        if self.at_sym(value):
            return self.next()
        return None

    def expect_sym(self, value: str) -> Token:
        t = self.peek()
        if t is None or t.kind != SYMBOL or t.value != value:
            got = t.value if t is not None else "<eof>"
            raise GrammarParseError(f"expected {value!r}, got {got!r} at line "
                                    f"{t.line if t else '?'}")
        return self.next()

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
        hdr = self._parse_header()
        body = self._parse_body(hdr)
        r = {"kind": "rule", "name": hdr.name, "rule_kind": hdr.kind,
             "returns": hdr.returns, "override": hdr.override,
             "source": {"file": self.source, "line": hdr.line},
             "body": body}
        return r

    def _parse_header(self) -> RuleHeader:
        c = self.c
        # optional 'fragment' or 'enum' or 'terminal'/'terminal fragment'
        kind = "rule"
        t = c.peek()
        if t is not None and t.value in ("fragment", "enum", "terminal"):
            kw = c.next().value
            if kw == "terminal":
                # may be 'terminal fragment NAME'
                if c.at("fragment"):
                    c.next()
                kind = "terminal"
            elif kw == "enum":
                kind = "enum"
            else:  # fragment
                kind = "fragment"
        # @Override may precede the name; tolerates both a combined
        # '@Override' IDENT (this lexer) and '@' + 'Override' tokens.
        override = False
        while c.peek() is not None and c.peek().value in ("@Override", "@"):
            override = True
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
            returns = "::".join(parts) if parts else None
        # optional ':' (rule body) or ';' (empty)
        c.expect(":")
        return RuleHeader(kind, name, returns, name_tok.line, override)

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
            while c.at_sym("|"):
                c.next()
                e2 = self._parse_element()
                if e2 is not None:
                    items.append(e2)
        # consume the rule-terminating ';' if present
        c.accept_sym(";")
        return _clean_seq(items)

    def _collect_until_semicolon(self) -> list[dict]:
        out = []
        while True:
            t = self.c.peek()
            if t is None or (t.kind == SYMBOL and t.value == ";"):
                break
            self.c.next()
            out.append({"kind": _tok_kind(t), "value": t.value, "line": t.line})
        self.c.accept_sym(";")
        return out

    # -- element ---------------------------------------------------------
    def _parse_element(self) -> dict | None:
        c = self.c
        t = c.peek()
        while t is not None and c.at_sym("|"):
            c.next()
            t = c.peek()
        if t is None:
            return None
        # alternation: collect 'primary' separated by '|'
        first = self._parse_primary()
        if c.at_sym("|"):
            choices = [first]
            while c.at_sym("|"):
                c.next()
                p = self._parse_primary()
                if p is not None:
                    choices.append(p)
            return _add_cardinality(_node("alt", first["line"], choices=choices), c)
        return first

    def _parse_primary(self) -> dict | None:
        c = self.c
        t = c.peek()
        if t is None:
            return None
        # skip leading '|'
        while c.at_sym("|"):
            c.next()
        items = []
        while True:
            e = self._parse_factor()
            if e is None:
                break
            items.append(e)
            # stop if we hit a top-level alternative or body end
            if c.at_sym("|") or t_is_end(c):
                break
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        return _node("seq", items[0]["line"], items=items)

    def _parse_factor(self) -> dict | None:
        c = self.c
        t = c.peek()
        if t is None or t_is_end(c) or c.at_sym("|"):
            return None
        line = t.line

        # literal
        if t.kind == LITERAL:
            c.next()
            return _add_cardinality(_node("lit", line, value=t.value), c)

        # parenthesized group
        if c.at_sym("("):
            c.next()
            inner = self._parse_group_inner(line)
            c.expect_sym(")")
            return _add_cardinality(_node("group", line, body=inner), c)

        # action {TypeName} or {TypeName.feature = current}
        if c.at_sym("{"):
            c.next()
            parts = []
            while True:
                t2 = c.peek()
                if t2 is None or (t2.kind == SYMBOL and t2.value == "}"):
                    break
                c.next()
                parts.append(t2.value)
            c.expect_sym("}")
            return _node("action", line, type=" ".join(parts))

        # assignment: name op value
        if t.kind == IDENT and c.peek(1) is not None and c.peek(1).kind == SYMBOL \
                and c.peek(1).value in ("=", "+=", "?=", ":="):
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
            body = self._parse_factor()
            return _node("pred", line, arrow=arrow, body=body)

        # xref [Type|Name]
        if c.at_sym("["):
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
            if c.at_sym("|"):
                c.next()
                ref = self._parse_xref_ref()
            c.expect_sym("]")
            return _add_cardinality(
                _node("xref", line, type="::".join(parts), ref=ref), c)

        # rule call (ident)
        if t.kind == IDENT:
            c.next()
            return _add_cardinality(_node("call", line, name=t.value), c)

        # any other symbol: treat as literal-ish passthrough token
        c.next()
        return _node("tok", line, value=t.value)

    def _parse_group_inner(self, line: int) -> dict:
        """Parse the inside of a parenthesized group: a sequence or an
        alternation.  Returns an `alt` node if there are `|` alternatives,
        else a single element or an empty `seq`."""
        c = self.c
        first = self._parse_primary()
        if first is None:
            return _node("seq", line, items=[])
        if not c.at_sym("|"):
            return first
        choices = [first]
        while c.at_sym("|"):
            c.next()
            p = self._parse_primary()
            if p is not None:
                choices.append(p)
        return _node("alt", line, choices=choices)

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
        if c.at_sym("("):
            c.next()
            inner = self._parse_group_inner(t.line)
            c.expect_sym(")")
            return _node("group", t.line, body=inner)
        # literal
        if t.kind == LITERAL:
            c.next()
            return _node("lit", t.line, value=t.value)
        # xref
        if c.at_sym("["):
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
            if c.at_sym("|"):
                c.next()
                ref = self._parse_xref_ref()
            c.expect_sym("]")
            return _node("xref", t.line, type="::".join(parts), ref=ref)
        # ident call
        if t.kind == IDENT:
            c.next()
            return _node("call", t.line, name=t.value)
        # otherwise passthrough
        c.next()
        return _node("tok", t.line, value=t.value)


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
