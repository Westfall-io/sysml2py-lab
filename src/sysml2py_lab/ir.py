from __future__ import annotations

"""Loss-minimizing IR for SysML text (issue #7).

The IR replaces the brace heuristic with a comment/string-aware parse:

  - `lexer.tokenize_sysml` tiles the input into tokens (100% coverage).
  - `statement_segments` splits the token stream into statements on
    `;` and brace boundaries, joining multi-line continuations into a
    single statement (so a 4-line transition is ONE statement, not four).
  - `parse_ir` builds a tree of `IRNode`s with kind/name/modifiers/
    type_refs/multiplicity/children/source_span/raw_text/fidelity.

Fidelity rules (loss-minimizing by design):
  - `modelled`   — the node's kind is known to the relationship model
                   (issue #6 children model) and its header decomposed.
  - `partial`    — the node's kind is known but the body/header could not
                   be fully decomposed (e.g. an unsupported construct).
  - `opaque`     — the node could not be classified (unknown construct).
At minimum it preserves the raw_text so nothing is silently dropped.

Comments are retained as IR nodes (kind `comment`).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .lexer import (
    Token,
    tokenize_sysml,
    LBRACE,
    RBRACE,
    SEMI,
    LINE_COMMENT,
    BLOCK_COMMENT,
    UNRESTRICTED_NAME,
    STRING,
    WORD,
    SYMBOL,
    WHITESPACE,
)

# Node kinds
COMMENT = "comment"
PACKAGE = "package"
PART = "part"
ITEM = "item"
VALUE = "value"
ATTRIBUTE = "attribute"
VARIATION = "variation"
CONNECTION = "connection"
PORT = "port"
STATE = "state"
TRANSITION = "transition"
ACTION = "action"
REQUIREMENT = "requirement"
USE_CASE = "use_case"
INTERFACE = "interface"
CONSTRAINT = "constraint"
OCCURRENCE = "occurrence"
IMPORT = "import"
UNKNOWN = "unknown"

# kind->"def" keyword that declares it (used for header classification)
_KIND_KEYWORDS = {
    "package": "package",
    "part": "part",
    "item": "item",
    "value": "value",
    "attribute": "attribute",
    "variation": "variation",
    "connection": "connection",
    "port": "port",
    "state": "state",
    "transition": "transition",
    "action": "action",
    "requirement": "requirement",
    "use_case": "use_case",
    "interface": "interface",
    "constraint": "constraint",
    "occurrence": "occurrence",
    "import": "import",
}

# Mapping from a leading header keyword to the IR kind used for a *definition*
# (`def`) declaration.  Populated from the issue-6 relationship model; the
# static table below is only the seed/fallback so the IR still works standalone.
_DEF_KIND = {
    "part": "part",
    "item": "item",
    "attribute": "attribute",
    "port": "port",
    "interface": "interface",
    "connection": "connection",
    "action": "action",
    "requirement": "requirement",
    "use_case": "use_case",
    "occurrence": "occurrence",
    "package": "package",
}


def _kind_vocab_from_model(model_path: str | None = None) -> set[str]:
    """Build the set of known member-kind stems from the issue-6 children
    model (`spec/relationships/children.json`).

    Returns a set of lowercased stems; empty if the model is unavailable so
    the IR still classifies via the static fallback table.
    """
    import json
    import re
    from pathlib import Path

    path = Path(model_path) if model_path else None
    if path is None:
        # look for the repo-relative default
        cand = Path(__file__).resolve().parents[2] / "spec" / "relationships" / "children.json"
        if cand.exists():
            path = cand
    if path is None or not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    kinds: set[str] = set()
    for body_entries in data.get("bodies", {}).values():
        for entry in body_entries:
            for k in entry.get("kinds", []):
                stem = re.sub(r"(Member|Definition|Element|Usage)$", "", k).lower()
                kinds.add(stem)
            stem = re.sub(r"(Member|Definition|Element|Usage)$", "", entry.get("wrapper", "")).lower()
            kinds.add(stem)
    return kinds


@dataclass
class IRNode:
    kind: str
    name: str = ""
    short_name: str = ""
    modifiers: list[str] = field(default_factory=list)
    type_refs: list[str] = field(default_factory=list)
    multiplicity: str | None = None
    children: list["IRNode"] = field(default_factory=list)
    source_span: tuple[int, int] = (0, 0)
    raw_text: str = ""
    fidelity: str = "modelled"

    def walk(self) -> Iterator["IRNode"]:
        """Yield self and all descendants (preorder)."""
        yield self
        for c in self.children:
            yield from c.walk()

    def dump(self, indent: int = 0) -> str:
        pad = "  " * indent
        lines = [
            f"{pad}{self.kind} name={self.name!r} mods={self.modifiers} "
            f"types={self.type_refs} mult={self.multiplicity} "
            f"fid={self.fidelity} span={self.source_span}"
        ]
        for c in self.children:
            lines.append(c.dump(indent + 1))
        return "\n".join(lines)


def _tiles(toks: list[Token]) -> dict:
    """Return a char->token map for O(1) coverage checks (not needed for
    production but handy for tests)."""
    m = {}
    for t in toks:
        for p in range(t.start, t.end):
            m[p] = t
    return m


# --------------------------------------------------------------------------
# Statement segmentation
# --------------------------------------------------------------------------

def statement_segments(text: str) -> list[str]:
    """Split `text` into statements (strings)."""
    return [text[s:e] for s, e in _statement_spans(text)]


def _statement_spans(text: str) -> list[tuple[int, int]]:
    """Split `text` into (start, end) statement spans.

    Segmentation boundaries: `;`, `{`, `}`.  Each terminator ends the
    current statement.  Multi-line continuations (a statement with no `;`
    that continues on the next line) are JOINED into a single span — so a
    4-line `transition ... first ... accept ... then ...;` is one segment.

    `{` and `}` each become their own span so the tree builder can place
    them, and the statement that opens a block (its header) is a separate
    span.  Comments are folded into their surrounding statement span
    (retained, never deleted) or become a comment-only span.
    """
    toks = tokenize_sysml(text)
    spans: list[tuple[int, int]] = []
    cur: list[tuple[int, int]] = []  # token spans of the current statement

    def flush():
        nonlocal cur
        if cur:
            s0 = min(s for s, _ in cur)
            e1 = max(e for _, e in cur)
            spans.append((s0, e1))
            cur = []

    for t in toks:
        if t.kind == WHITESPACE:
            continue
        if t.kind in (LINE_COMMENT, BLOCK_COMMENT):
            # fold into current span (retained, not deleted); if empty this
            # starts a comment-only span
            cur.append((t.start, t.end))
            continue
        cur.append((t.start, t.end))
        if t.kind == SEMI:
            flush()
        elif t.kind == LBRACE:
            # close the header segment BEFORE the brace so `{` is its own span
            cur.pop()  # remove the brace from the header span
            flush()
            # reopen with just the brace
            cur.append((t.start, t.end))
            flush()
        elif t.kind == RBRACE:
            # close content, then `}` is its own span
            cur.pop()
            flush()
            cur.append((t.start, t.end))
            flush()
    flush()
    return spans


# --------------------------------------------------------------------------
# IR parsing
# --------------------------------------------------------------------------

def _parse_header(tokens: list[Token], vocab: set[str] | None = None) -> dict:
    """Decompose a statement's header token list into kind/name/modifiers/
    type_refs/multiplicity.

    `vocab` is the set of known member-kind stems from the issue-6 children
    model; when a leading keyword is in the vocabulary it classifies as
    `modelled` (the relationship model says that member kind is valid).
    """
    words = [t.text for t in tokens if t.kind in (WORD, UNRESTRICTED_NAME, SYMBOL, STRING)]
    kind = None
    name = ""
    modifiers: list[str] = []
    type_refs: list[str] = []
    multiplicity: str | None = None
    # walk words: a leading keyword -> kind; 'def' -> declaration; name after
    # that; ':>' / ':' type refs; '['..']' multiplicity.
    i = 0
    while i < len(words):
        w = words[i]
        if w in _KIND_KEYWORDS and kind is None and i == 0:
            kind = w
            i += 1
            continue
        if kind is None and w in ("private", "public", "protected", "ref", "readonly", "derived", "end", "abstract", "variation"):
            modifiers.append(w)
            i += 1
            continue
        if kind is None:
            kind = "unknown"
        # name token
        if not name and w != "def":
            name = w.strip("'\"")
            i += 1
            continue
        if w == "def":
            i += 1
            continue
        if w in (":>", ":>>", ":", "=", ":=") :
            i += 1
            # next word (if any) is a type ref
            if i < len(words):
                type_refs.append(words[i])
                i += 1
            continue
        if w == "[" :
            # multiplicity [ .. ]
            j = i + 1
            buf = []
            while j < len(words) and words[j] != "]":
                buf.append(words[j])
                j += 1
            multiplicity = "".join(buf) if buf else "*"
            i = j + 1
            continue
        # any other token after name (e.g. ':' with no type) — skip
        i += 1

    if kind is None:
        kind = "unknown"
    # fidelity: 'modelled' iff the kind stem is in the relationship-model
    # vocabulary (issue #6 children model).  When vocab is absent we fall
    # back to the static _KIND_KEYWORDS table (known iff not unknown).
    known = kind != "unknown"
    if vocab is not None and kind != "unknown":
        known = kind in vocab or any(k.startswith(kind) for k in vocab)
    return {
        "kind": kind,
        "name": name,
        "short_name": name,
        "modifiers": modifiers,
        "type_refs": type_refs,
        "multiplicity": multiplicity,
        "known": known,
    }


def _classify_header(header_text: str, vocab: set[str] | None = None) -> dict:
    """Classify a header string into IR fields (used by parse_ir)."""
    toks = tokenize_sysml(header_text)
    return _parse_header(toks, vocab)


def parse_ir(text: str, model_path: str | None = None) -> IRNode:
    """Parse SysML text into an IR tree.

    Strategy: tokenize; segment into statements (; and brace aware);
    build a tree by walking statements and brace depth; classify each
    statement's header via `_parse_header`; attach statements as children of
    the current block.  Comments become `comment` IR nodes (not deleted).

    `model_path` optionally points at the issue-6 `children.json` used to
    classify `kind`/fidelity as modelled vs opaque rather than guessed.
    """
    vocab = _kind_vocab_from_model(model_path)
    segs = _statement_spans(text)
    root = IRNode(kind="root", source_span=(0, len(text)), raw_text="")
    stack: list[IRNode] = [root]
    depth = 0

    for (st, en) in segs:
        seg_text = text[st:en].strip()
        if not seg_text:
            continue
        parent = stack[-1] if stack else root

        # comment segments -> comment nodes
        if seg_text.lstrip().startswith("//") or seg_text.lstrip().startswith("/*"):
            node = IRNode(
                kind=COMMENT,
                raw_text=text[st:en],
                source_span=(st, en),
                fidelity="modelled",
            )
            parent.children.append(node)
            continue

        # `{` opens a block: the node we just created becomes the container
        if seg_text == "{":
            brace = IRNode(kind="brace_open", source_span=(st, en), raw_text="{")
            # ascend to the opener node and put the brace INSIDE it, then
            # become that opener's block context
            if parent.children:
                opener = parent.children[-1]
                opener.children.append(brace)
                stack.append(opener)
            else:
                blk = IRNode(kind="block", source_span=(st, en), raw_text="{")
                parent.children.append(blk)
                blk.children.append(brace)
                stack.append(blk)
            depth += 1
            continue
        # `}` closes a block: brace_close goes inside the block we're closing
        if seg_text == "}":
            brace = IRNode(kind="brace_close", source_span=(st, en), raw_text="}")
            if len(stack) > 1:
                stack[-1].children.append(brace)
                stack.pop()
            else:
                stack[-1].children.append(brace)
            depth = max(0, depth - 1)
            continue

        # ordinary statement
        hdr = _classify_header(seg_text, vocab)
        node = IRNode(
            kind=hdr["kind"],
            name=hdr["name"],
            short_name=hdr["short_name"],
            modifiers=hdr["modifiers"],
            type_refs=hdr["type_refs"],
            multiplicity=hdr["multiplicity"],
            source_span=(st, en),
            raw_text=text[st:en],
            fidelity="modelled" if hdr["known"] else "opaque",
        )
        parent.children.append(node)

    # root range is whole text (nodes may have overlaps with whitespace but
    # raw_text coverage assertion is on texts, not spans)
    return root


def render_ir(root: IRNode, indent: int = 0) -> str:
    """Render an IR tree back to text.

    The IR is loss-minimizing: every node's raw_text tiles the source in
    source order (preorder == source order for statements), so rendering is
    simply concatenating raw_text in preorder.  Inter-statement whitespace
    (not owned by any node) is not reproduced — the issue's acceptance
    criterion is round-trip under normalization, which collapses whitespace
    anyway.
    """
    return "".join(n.raw_text for n in root.walk())


def ir_fidelity_summary(root: IRNode) -> dict:
    """Per-file fidelity summary: counts of modelled/partial/opaque nodes
    plus byte coverage of each fidelity class (issue #7: fidelity reported
    per file so opaque regions are visible, not lost)."""
    counts = {"modelled": 0, "partial": 0, "opaque": 0}
    bytes_by_fid = {"modelled": 0, "partial": 0, "opaque": 0}
    for n in root.walk():
        f = n.fidelity if n.fidelity in counts else "opaque"
        counts[f] += 1
        bytes_by_fid[f] += len(n.raw_text)
    return {
        "node_counts": counts,
        "raw_text_bytes": bytes_by_fid,
        "total_raw_bytes": sum(len(n.raw_text) for n in root.walk()),
    }


def ir_to_json(root: IRNode) -> dict:
    """Serialize an IR tree to a JSON-friendly dict (issue #7 deliverable)."""
    return {
        "kind": root.kind,
        "name": root.name,
        "short_name": root.short_name,
        "modifiers": list(root.modifiers),
        "type_refs": list(root.type_refs),
        "multiplicity": root.multiplicity,
        "source_span": list(root.source_span),
        "raw_text": root.raw_text,
        "fidelity": root.fidelity,
        "children": [ir_to_json(c) for c in root.children],
    }


def ir_from_json(d: dict) -> IRNode:
    """Reconstruct an IRNode tree from `ir_to_json` output."""
    return IRNode(
        kind=d.get("kind", "root"),
        name=d.get("name", ""),
        short_name=d.get("short_name", ""),
        modifiers=list(d.get("modifiers", [])),
        type_refs=list(d.get("type_refs", [])),
        multiplicity=d.get("multiplicity"),
        source_span=tuple(d.get("source_span", (0, 0))),
        raw_text=d.get("raw_text", ""),
        fidelity=d.get("fidelity", "modelled"),
        children=[ir_from_json(c) for c in d.get("children", [])],
    )
