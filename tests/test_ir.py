from __future__ import annotations

"""Issue #7 — loss-minimizing IR + real corpus parser.

RED tests for the comment/string-aware lexer and the IR node model.  The
acceptance criteria this module guards:
  - comment/string-aware lexing (//, /* */, 'unrestricted names', "strings")
  - 100% byte attribution (every input byte lands in exactly one token/node)
  - statement segmentation on `;` and brace boundaries with multi-line join
  - comments retained as IR nodes, not deleted
  - fidelity reported per node (modelled|partial|opaque)
  - IR -> text round-trips under normalization
"""

import re

from pathlib import Path

import pytest

from sysml2py_lab.ir import (
    IRNode,
    parse_ir,
    render_ir,
    statement_segments,
)
from sysml2py_lab.lexer import (
    Token,
    tokenize_sysml,
    token_kinds,
)

_WS = re.compile(r"\s+")


def _nws(s: str) -> str:
    """Whitespace-insensitive comparison key (all whitespace incl. tabs)."""
    return _WS.sub("", s)


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------

def test_lexer_comment_string_aware():
    """The lexer must NOT treat braces inside comments or strings as block
    openers."""
    src = "part A { /* a { not a block */ b; }"
    toks = tokenize_sysml(src)
    kinds = [t.kind for t in toks]
    # '/*' and '*/' comment tokens; the '{' inside the comment is NOT a brace
    assert "BLOCK_COMMENT" in kinds
    assert kinds.count("LBRACE") == 1  # only the real open '{'
    assert kinds.count("RBRACE") == 1  # only the real close '}'
    assert "{ not a block" in src  # sanity anchor


def test_lexer_line_comment():
    src = "part a; // trailing\npart b;"
    toks = tokenize_sysml(src)
    kinds = [t.kind for t in toks]
    assert "LINE_COMMENT" in kinds
    # the '// trailing' text is captured as a comment token, not dropped
    assert any(t.kind == "LINE_COMMENT" and "trailing" in t.text for t in toks)


def test_lexer_unrestricted_name_and_string():
    """'unrestricted names' and "strings" are single tokens; their inner
    spaces/quotes don't split them."""
    src = "part 'Agent A' : 'Person type';\nvalue x = \"a \\\"quoted\\\" string\";"
    toks = tokenize_sysml(src)
    texts = [t.text for t in toks]
    assert any(t.startswith("'Agent A'") for t in texts)
    assert any(t.startswith("\"a \\\"quoted\\\" string\"") for t in texts)


def test_lexer_100pct_byte_coverage():
    """Every input byte must belong to exactly one token — coverage by
    construction."""
    src = (
        "package P { // c1\n"
        "  /* block { with brace */\n"
        "  part 'Agent A' : T[1] { attribute x; }\n"
        "  s = \"str { }\";\n"
        "}\n"
    )
    toks = tokenize_sysml(src)
    # reconstruct positions; tokens must tile the input with no gap/overlap
    pos = 0
    for t in toks:
        assert t.start == pos, f"gap at {pos}: token {t.text!r} starts at {t.start}"
        assert t.end > t.start
        pos = t.end
    assert pos == len(src), f"unattributed trailing bytes: {len(src) - pos}"


def test_lexer_token_spans():
    src = "part abc;"
    toks = tokenize_sysml(src)
    assert toks[0].text == "part"
    assert toks[0].start == 0
    assert toks[0].end == 4
    assert src[toks[0].start:toks[0].end] == "part"


# --------------------------------------------------------------------------
# Statement segmentation
# --------------------------------------------------------------------------

def test_segmentation_on_semicolon_and_join():
    """`action def ParseMessage {in x; out y;}` (one line) must split into
    three statements; a multi-line transition must join into one."""
    one_line = "action def ParseMessage {in x; out y;}"
    segs = statement_segments(one_line)
    # 'action def ParseMessage', '{', 'in x;', 'out y;', '}' as statements
    assert len(segs) == 5, [s.strip() for s in segs]
    assert any(s.strip().startswith("action def ParseMessage") for s in segs)
    assert any(s.strip() == "in x;" for s in segs)
    assert any(s.strip() == "out y;" for s in segs)

    transition = (
        "transition asleep_to_awake\n"
        "\tfirst asleep\n"
        "\taccept after 8[h]\n"
        "\tthen awake;\n"
    )
    joined = statement_segments(transition)
    single = [s for s in joined if "asleep_to_awake" in s]
    assert len(single) == 1, [s.strip() for s in joined]
    assert "first asleep" in single[0]
    assert "accept after 8[h]" in single[0]
    assert "then awake" in single[0]


def test_segmentation_does_not_split_string_content():
    src = 'x = "a; b"; y;'
    segs = statement_segments(src)
    assert any('"a; b"' in s for s in segs)
    assert any(s.strip() == "y;" for s in segs)


# --------------------------------------------------------------------------
# IR tree
# --------------------------------------------------------------------------

def test_ir_node_fields():
    """IR nodes carry kind, name, short_name, modifiers, type_refs,
    multiplicity, children, source_span, raw_text, fidelity."""
    n = IRNode(
        kind="part",
        name="adult",
        short_name="adult",
        modifiers=["private"],
        type_refs=["Person"],
        multiplicity="*",
        children=[],
        source_span=(0, 30),
        raw_text="part adult[*] : Person;",
        fidelity="modelled",
    )
    assert n.kind == "part"
    assert n.name == "adult"
    assert n.modifiers == ["private"]
    assert n.type_refs == ["Person"]
    assert n.multiplicity == "*"
    assert n.fidelity == "modelled"


def test_ir_retains_comments_as_nodes():
    """Comments are IR nodes, not deleted."""
    src = "part a; // c1\npart b;\n"
    root = parse_ir(src)
    comment_nodes = [
        c for c in root.walk()
        if c.kind in ("comment", "line_comment", "block_comment")
    ]
    assert comment_nodes, "no comment IR nodes found"
    assert any("c1" in c.raw_text for c in comment_nodes)


def test_ir_100pct_byte_coverage():
    """Every input byte is attributable to exactly one IR node (raw_text)."""
    src = (
        "package P {\n"
        "  part a : T; // c\n"
        "  state s { entry; then awake; }\n"
        "}\n"
    )
    root = parse_ir(src)
    # concatenate all raw_text segments; must tile the input exactly
    segs = [n.raw_text for n in root.walk() if n.raw_text]
    # every node's raw_text should be contiguous coverage: simplest strong
    # check is that removing ALL whitespace and joining nodes reproduces the
    # non-whitespace source (whitespace may live in raw_text spans).
    concat = _nws("".join(segs))
    assert concat == _nws(src), "every input byte must be in exactly one IR node"


def test_ir_fidelity_and_roundtrip_family():
    """family.sysml parses to IR with all bytes attributed, and IR -> text
    reproduces it under normalization."""
    fam = (Path(__file__).resolve().parents[1] / "examples" / "family.sysml")
    if not fam.exists():
        pytest.skip("family.sysml not present")
    src = fam.read_text(encoding="utf-8")
    root = parse_ir(src)
    # 100% byte coverage
    segs = [n.raw_text for n in root.walk() if n.raw_text]
    concat = _nws("".join(segs))
    assert concat == _nws(src), "every input byte must be in exactly one IR node"
    # round-trip: whitespace-insensitive equality (canonical normalization is
    # issue #8's scope; for #7 the loss-minimizing proof is byte coverage +
    # content equality under whitespace collapse)
    rendered = render_ir(root)
    assert _nws(rendered) == _nws(src)
    # fidelity is populated on nodes (at least root is modelled)
    assert root.fidelity in ("modelled", "partial", "opaque")
    # comments retained
    kinds = {n.kind for n in root.walk()}
    assert kinds & {"comment", "line_comment", "block_comment"}, "comments dropped"


def test_kind_from_relationship_model():
    """kind/known (fidelity) derive from the issue-6 children model, not a
    hard-coded guess (round-3 C1): a kind ONLY in the model vocabulary (not
    in the static table) must be derived as a typed kind, and the model must
    change classification vs relying on the static table alone."""
    from sysml2py_lab.ir import _parse_header, _kind_vocab_from_model, _KIND_KEYWORDS
    # 'part' is a known member kind in the children model (and static table)
    hdr = _parse_header(tokenize_sysml("part adult[1] : Person;"),
                        vocab=set(_kind_vocab_from_model()))
    assert hdr["kind"] == "part"
    assert hdr["multiplicity"] == "1"
    assert hdr["type_refs"] == ["Person"]
    assert hdr["known"] is True
    # a kind ONLY in the model (actor/subject are in children.json but NOT in
    # the static 16-keyword table) must be DERIVED from the model — this is
    # the round-3 C1 test that fails if the model path is dead/vacuous.
    assert "actor" not in _KIND_KEYWORDS, "test precondition: actor not static"
    for k in ("actor", "subject", "objective", "message"):
        h = _parse_header(tokenize_sysml(f"{k} x;"), vocab=set(_kind_vocab_from_model()))
        assert h["kind"] == k, f"model kind {k} not derived (got {h['kind']}, known={h['known']})"
        assert h["known"] is True
        assert h["name"] == "x"
    # without the model, those same headers must NOT be derived to that kind
    # (only the static fallback applies)
    h_nomodel = _parse_header(tokenize_sysml("actor x;"), vocab=None)
    assert h_nomodel["kind"] != "actor", "actor should not classify without the model"
    # a genuine unknown (not in model, not in static) stays opaque
    h2 = _parse_header(tokenize_sysml("wibble x;"), vocab=set(_kind_vocab_from_model()))
    assert h2["known"] is False


def test_fidelity_opaque_for_unknown():
    """An unrecognized construct is preserved as an `opaque` node (loss-
    minimizing: visible, not deleted)."""
    root = parse_ir("part a;\nthingamajig foo { bar baz; }\npart b;")
    opacity = [n for n in root.walk() if n.fidelity == "opaque"]
    assert opacity, "expected at least one opaque node"
    assert any("thingamajig" in n.raw_text for n in opacity)


def test_ir_json_roundtrip():
    """ir_to_json -> ir_from_json preserves the tree (issue #7 deliverable:
    IR JSON serializer)."""
    from sysml2py_lab.ir import ir_to_json, ir_from_json
    src = "package P { part a : T; state s { entry; then awake; } }"
    root = parse_ir(src)
    back = ir_from_json(ir_to_json(root))
    # kind/name preserved and structure equal
    assert back.kind == root.kind
    assert _nws(render_ir(back)) == _nws(render_ir(root))
    # all nodes preserved (same count)
    assert sum(1 for _ in back.walk()) == sum(1 for _ in root.walk())


def test_family_kind_presence():
    """W11(a) regression: the named declarations in family.sysml must land as
    typed IR nodes — the suite must catch a declaration swallowed into a
    comment or misclassified as unknown."""
    fam = (Path(__file__).resolve().parents[1] / "examples" / "family.sysml")
    if not fam.exists():
        pytest.skip("family.sysml not present")
    root = parse_ir(fam.read_text(encoding="utf-8"))
    kinds = {n.kind for n in root.walk()}
    # the file defines all of these
    missing = {"part", "connection", "requirement", "occurrence",
               "constraint", "use_case", "interface", "action",
               "attribute", "item", "port", "state", "transition",
               "package", "import"} - kinds
    assert not missing, f"kinds missing from family.sysml IR: {missing}"
    # the named declarations specifically must exist as nodes with names
    names = {n.name for n in root.walk() if n.name}
    for expected in ("Person", "Child", "ProcessMessage",
                     "LegalAdoptionParenthood", "AdoptionCertification",
                     "VerbalInteraction", "minimumAgeForAdoptiveParenthood",
                     "Agree on adoption"):
        assert expected in names, f"declaration {expected!r} missing from IR names"


def _code_semis(text: str) -> int:
    """Count statement terminators (`;`) OUTSIDE comments/strings, so a block
    comment containing ';' in prose doesn't count as multiple statements."""
    from sysml2py_lab.lexer import tokenize_sysml, SEMI
    return sum(1 for t in tokenize_sysml(text) if t.kind == SEMI)


def test_no_multi_statement_node():
    """W11(b) regression: no IR node's raw_text may contain more than one CODE
    statement terminator (';' outside comments/strings) — a node must never
    silently merge statements (C2)."""
    fam = (Path(__file__).resolve().parents[1] / "examples" / "family.sysml")
    if not fam.exists():
        pytest.skip("family.sysml not present")
    root = parse_ir(fam.read_text(encoding="utf-8"))
    for n in root.walk():
        if _code_semis(n.raw_text) > 1:
            raise AssertionError(
                f"node kind={n.kind} name={n.name!r} holds multiple statements: {n.raw_text[:80]!r}")


def test_span_tiling_no_overlap():
    """W11(c) regression: every byte position belongs to at most one IR node
    (Counter over source spans has no value > 1) — catches W4's double-count.
    The root container's full-file span is ignored (it owns no bytes)."""
    from collections import Counter
    fam = (Path(__file__).resolve().parents[1] / "examples" / "family.sysml")
    if not fam.exists():
        pytest.skip("family.sysml not present")
    src = fam.read_text(encoding="utf-8")
    root = parse_ir(src)
    c = Counter()
    for n in root.walk():
        if n.kind == "root":
            continue  # container span covers the file; owns no raw bytes
        s, e = n.source_span
        if e > s:
            c.update(range(s, e))
    overlaps = {pos: cnt for pos, cnt in c.items() if cnt > 1}
    assert not overlaps, f"overlapping IR spans at byte positions: {list(overlaps)[:10]}"
    # the anonymous-block path must also tile cleanly (r2 W2: the W4 fix and
    # its residual span duplication are NOT exercised by family.sysml)
    for probe in ("{ part a; }", "{}{}", "part a { x; } { y; }"):
        p = parse_ir(probe)
        pc = Counter()
        for n in p.walk():
            if n.kind == "root":
                continue
            s, e = n.source_span
            if e > s:
                pc.update(range(s, e))
        assert not {pos: cnt for pos, cnt in pc.items() if cnt > 1}, \
            f"overlapping spans in {probe!r}"


def test_no_opaque_with_known_kind():
    """Round-3 regression: (a) a node with a recognized kind must NEVER report
    `opaque` ('opaque' means "could not be classified" — an opaque node with a
    known kind is a fidelity lie); (b) a kind must actually be DERIVED from the
    relationship-model vocabulary (round-3 C1/C2: assert model-only kinds
    classify against a vocab that lacks every static keyword, so a reverted
    vocab path fails in isolation)."""
    from sysml2py_lab.ir import _parse_header, _kind_vocab_from_model, _KIND_KEYWORDS
    # (b) behavioural pin: vocab holds ONLY model-only kinds (none static), so
    # deriving them is provably the model's doing, not the static table's.
    model_vocab = set(_kind_vocab_from_model())
    model_only = {k for k in model_vocab if k not in _KIND_KEYWORDS}
    assert "usecase" in model_vocab, "precondition: usecase in model vocab"
    assert "actor" in model_only, "precondition: actor is model-only"
    # `usecase` is the vocab-dependent spelling (W3: the 'use case' two-word
    # form is hard-coded so it cannot test the vocab path — this CAN).
    h = _parse_header(tokenize_sysml("usecase u;"), vocab=model_only | {"usecase"})
    assert h["kind"] == "usecase", f"usecase not derived from model: {h}"
    assert h["known"] is True
    h2 = _parse_header(tokenize_sysml("actor x;"), vocab=model_only | {"actor"})
    assert h2["kind"] == "actor" and h2["known"] is True
    # (a) the invariant: no opaque node in family.sysml carries a known kind
    fam = (Path(__file__).resolve().parents[1] / "examples" / "family.sysml")
    if not fam.exists():
        pytest.skip("family.sysml not present")
    root = parse_ir(fam.read_text(encoding="utf-8"))
    lies = [(n.kind, n.name, n.raw_text[:40]) for n in root.walk()
            if n.fidelity == "opaque" and n.kind != "unknown"
            and n.kind not in ("root", "brace_open", "brace_close", "block", "comment")]
    assert not lies, f"opaque nodes with known kind: {lies}"


def test_no_reserved_kind_from_model():
    """Round-4 C1 regression: the children-model vocab contains `comment`,
    which collides with the reserved structural comment kind — deriving it
    would label a merged-loss node `modelled` and suppress parent `partial`
    (a fidelity lie).  No derived kind may be a reserved structural kind, and
    the C1 repro must NOT report opaque-free modelled."""
    from sysml2py_lab.ir import _parse_header, _kind_vocab_from_model, ir_fidelity_summary
    vocab = set(_kind_vocab_from_model())
    assert "comment" in vocab, "precondition: comment is in the model vocab"
    # the reserved comment statement must NOT be derived from the model
    h = _parse_header(tokenize_sysml("comment C about X;"), vocab=vocab)
    assert h["kind"] != "comment", f"comment derived from model: {h}"
    assert h["known"] is False, "comment statement must stay opaque (reserved kind)"
    # snake_case identifiers must NOT be claimed as kinds (round-4 W2)
    for bad in ("data_type dt;", "item_flow f;", "state_action s;"):
        hb = _parse_header(tokenize_sysml(bad), vocab=vocab)
        assert hb["known"] is False, f"snake_case {bad!r} wrongly derived: {hb}"
    # the C1 repro: a merged comment+attribute must NOT report clean modelled
    src = "part def P { comment C about X /* hi */ attribute y; }"
    root = parse_ir(src)
    fids = {n.fidelity for n in root.walk()}
    assert "opaque" in fids, f"C1 repro must flag a loss, got fidelities {fids}"
    summ = ir_fidelity_summary(root)
    assert summ["node_counts"]["opaque"] >= 1, f"C1 repro must not be all-modelled: {summ}"


def test_opener_not_statement_terminated():
    """Round-3 W-A/W-B regression: a `;`-terminated or already-closed
    statement must NOT adopt a following block (structural misattribution is
    invisible to byte coverage)."""
    # consecutive blocks: second block is an anonymous block, not part of `a`
    root = parse_ir("part a { x; } { y; }")
    tops = [(n.kind, getattr(n, "name", "")) for n in root.children]
    assert ("block", "") in tops, f"expected anonymous block for 2nd block: {tops}"
    part_a = [n for n in root.children if n.kind == "part" and n.name == "a"][0]
    assert sum(1 for c in part_a.children if c.kind == "brace_close") == 1, \
        "part a must own exactly one body"
    # a ;-terminated statement must NOT adopt a following block (W-A)
    root2 = parse_ir("part a; { y; }")
    y_owner = None
    for n in root2.walk():
        if n.name == "y":
            y_owner = n
            parents = [c for c in root2.walk() if y_owner in c.children]
            assert not any(p.kind == "part" for p in parents), \
                "y must not be nested inside ;-terminated part a"
            break
    assert y_owner is not None, "y was not parsed at all"
    # package-level: part b must not nest inside ;-terminated part a (it may
    # sit in an anonymous block sibling to part a, both inside the package)
    root3 = parse_ir("package P { part a; { part b; } }")
    flat_b = [n for n in root3.walk() if n.kind == "part" and n.name == "b"]
    assert flat_b, "part b not parsed"
    b = flat_b[0]
    # walk up: no ancestor may be part a
    def _ancestors(node):
        return [p for p in root3.walk() if node in p.children]
    up = _ancestors(b)
    while up:
        assert up[0].name != "a", "part b must not nest inside part a"
        prev = up[0]
        up = _ancestors(prev) if prev.kind != "root" else []
    # b must be reachable from the package, not from a sibling part
    pkg = [n for n in root3.walk() if n.kind == "package" and n.name == "P"][0]
    assert any(n is b for n in pkg.walk()), "part b must live inside package P"
