"""Xtext grammar lexer for sysml2py-lab.

Tokenizes the small, regular Xtext grammar language used by the SysML v2
pilot .xtext files (KerML / SysML / KerMLExpressions).  It is deliberately
lenient: Xtext has many legacy/adornment tokens, and our goal is to classify
every token (zero unclassified) while preserving source order and line
provenance.

Tokens carry:
  kind   - one of the kinds below
  value  - the token text (for literals the *unquoted* body; for symbols the
           symbol itself)
  line   - 1-based source line
  col    - 1-based source column
"""

from __future__ import annotations

from dataclasses import dataclass


# --- token kinds --------------------------------------------------------
WS, NEWLINE, COMMENT, IDENT, LITERAL, STRING, SYMBOL = range(7)

KIND_NAMES = {
    WS: "ws",
    NEWLINE: "newline",
    COMMENT: "comment",
    IDENT: "ident",
    LITERAL: "literal",
    STRING: "string",
    SYMBOL: "symbol",
}


# One-char and multi-char symbols that carry grammar meaning.
# priority order: longest first
_SYM_ORDER = sorted(
    {"=>", "->", "?=", "+=", ":=", "..", "::", "<<", ">>", "==", "!="},
    key=len,
    reverse=True,
)
_SINGLE_SYMBOLS = set("{}()[]=:;,?*+|!<>&%.")


@dataclass(frozen=True)
class Token:
    kind: int
    value: str
    line: int
    col: int

    @property
    def kind_name(self) -> str:
        return KIND_NAMES[self.kind]


def tokenize(text: str) -> list[Token]:
    """Tokenize Xtext grammar source, dropping ws/newline/comment tokens.

    Returns a flat list of meaningful tokens only.  Comments (/* */, //)
    are skipped as whitespace.
    """
    toks: list[Token] = []
    i, n = 0, len(text)
    line, col = 1, 1

    def adv(s: str):
        nonlocal line, col
        # update line/col for the raw input, but we only track *start*
        # position per token below.
        for ch in s:
            if ch == "\n":
                line += 1
                col = 1
            else:
                col += 1

    while i < n:
        c = text[i]
        # whitespace
        if c in " \t":
            j = i
            while j < n and text[j] in " \t":
                j += 1
            adv(text[i:j])
            i = j
            continue
        if c == "\n":
            adv("\n")
            i += 1
            continue
        if c == "\r":
            # handle \r\n as one newline
            if i + 1 < n and text[i + 1] == "\n":
                adv("\r\n")
                i += 2
            else:
                adv("\r")
                i += 1
            continue

        # line comment //
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            if j == -1:
                j = n
            adv(text[i:j])
            i = j
            continue
        # block comment /* ... */
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j == -1:
                raise ValueError(f"unterminated block comment starting at line {line}")
            j += 2
            adv(text[i:j])
            i = j
            continue

        # string literal (single or double quoted)
        if c in ("'", '"'):
            start_line, start_col = line, col
            quote = c
            j = i + 1
            body = []
            closed = False
            while j < n:
                ch = text[j]
                if ch == "\\" and j + 1 < n:
                    # decode Xtext escapes to their logical character once;
                    # the emitter re-escapes for textX output.
                    esc = text[j + 1]
                    decoded = {
                        "t": "\t", "r": "\r", "n": "\n", "b": "\b", "f": "\f",
                        "\\": "\\", "'": "'", '"': '"',
                    }.get(esc)
                    if decoded is not None:
                        body.append(decoded)
                    else:
                        body.append(ch)  # unknown escape: keep the backslash
                    j += 2
                    continue
                if ch == quote:
                    closed = True
                    break
                body.append(ch)
                j += 1
            if not closed:
                raise ValueError(f"unterminated string literal starting at line {start_line}")
            j += 1  # closing quote
            toks.append(Token(LITERAL, "".join(body), start_line, start_col))
            adv(text[i:j])
            i = j
            continue

        # identifier / keyword
        if c.isalpha() or c == "_" or c == "@":
            start = i
            j = i
            if c == "@":
                j += 1
                while j < n and (text[j].isalnum() or text[j] == "_"):
                    j += 1
            else:
                while j < n and (text[j].isalnum() or text[j] == "_"):
                    j += 1
            toks.append(Token(IDENT, text[start:j], line, col))
            adv(text[start:j])
            i = j
            continue

        # digit starts
        if c.isdigit():
            start = i
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            toks.append(Token(IDENT, text[start:j], line, col))
            adv(text[start:j])
            i = j
            continue

        # multi-char symbols
        matched_sym = None
        for sym in _SYM_ORDER:
            if text.startswith(sym, i):
                matched_sym = sym
                break
        if matched_sym:
            toks.append(Token(SYMBOL, matched_sym, line, col))
            adv(matched_sym)
            i += len(matched_sym)
            continue

        # single-char symbols
        if c in _SINGLE_SYMBOLS:
            toks.append(Token(SYMBOL, c, line, col))
            adv(c)
            i += 1
            continue

        # anything else: classify as symbol token (unclassified fallback)
        toks.append(Token(SYMBOL, c, line, col))
        adv(c)
        i += 1

    return toks
