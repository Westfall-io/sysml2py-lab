from __future__ import annotations

"""Issue #8 — canonical normalizer (token-stream, alias-aware).

Guards the decision in spec/decisions/canonical-normalizer.md:

  - `part x` and `partx` must NOT compare equal (the strip_ws failure mode)
  - `specializes`/`subsets` == `:>`, `redefines` == `:>>` under the default
    (opt-in) alias table
  - `aliases={}` disables synonym resolution
  - whitespace AND comments are trivia
  - the module is self-contained (audited separately by the codegen test)
"""

from sysml2py_lab.normalize import (
    ALIASES,
    canonical_equals,
    canonical_tokens,
    canonicalize,
    normalize_text,
)


def test_partx_boundary_is_preserved():
    """The strip_ws failure: deleting whitespace compares `part x` == `partx`.
    The canonical token-stream normalizer must keep them different."""
    assert not canonical_equals("part x;", "partx;")
    assert not canonical_equals("part x", "partx")


def test_whitespace_is_trivia():
    assert canonical_equals("part x : T;", "part x:T;")
    assert canonical_equals("part  x   : T ;", "part x : T;")


def test_comments_are_trivia():
    assert canonical_equals("// hi\npart x;", "part x;")
    assert canonical_equals("part /* c */ x;", "part x;")
    # comment cannot inject a `;` or `{` into the significant stream
    assert canonical_equals("part x; // trailing", "part x;")


def test_multi_char_symbols_stay_single_tokens():
    """Review W4: `::`, `&&`, `**` etc. must not split into singles."""
    from sysml2py_lab.normalize import canonical_tokens
    assert canonical_tokens("import Package2::*;") == ["import", "Package2", "::", "*", ";"]
    assert canonical_tokens("a && b;") == ["a", "&&", "b", ";"]
    assert canonical_tokens("a ** b;") == ["a", "**", "b", ";"]
    assert canonical_tokens("a || b;") == ["a", "||", "b", ";"]
    assert canonical_tokens("a << b;") == ["a", "<<", "b", ";"]
    assert canonical_tokens("a .. b;") == ["a", "..", "b", ";"]


def test_aliases_resolve_synonyms_by_default():
    assert canonical_equals("a specializes b;", "a :> b;")
    assert canonical_equals("a subsets b;", "a :> b;")
    assert canonical_equals("a redefines b;", "a :>> b;")


def test_aliases_opt_out():
    assert not canonical_equals("a specializes b;", "a :> b;", aliases={})
    assert canonical_tokens("a specializes b;", aliases={}) == [
        "a", "specializes", "b", ";"]


def test_alias_table_is_explicit():
    assert ALIASES == {"specializes": ":>", "subsets": ":>", "redefines": ":>>"}


def test_canonicalize_is_stable():
    a = canonicalize("part  x : T; // c\npart y;")
    b = canonicalize("part x:T; part y;")
    assert a == b
    # comments are dropped, whitespace normalized
    assert a == "part x : T ; part y ;"


def test_quoted_names_keep_quotes():
    # unrestricted names are significant; the inner text is not split
    assert canonical_tokens("part 'my part';") == ["part", "'my part'", ";"]
    assert not canonical_equals("part 'my part';", "part 'my' 'part';")


def test_strings_keep_quotes():
    assert canonical_tokens('part a = "x y";') == ["part", "a", "=", '"x y"', ";"]


def test_braces_semicolons_are_significant():
    assert canonical_equals("package P { part x; }", "package P{part x;}")
    assert not canonical_equals("package P { part x; }", "package P { part x }")


def test_normalize_text_keeps_boundaries():
    # legacy text normalizer never merges tokens either
    assert normalize_text("part x;").replace(" ", "") != "partx;"


def test_block_comment_cannot_hide_braces():
    # the classic issue-#7 lexer guarantee, in the normalizer:
    # a `{` inside a block comment is trivia, not a significant token
    assert not canonical_equals("part a /* { */ x;", "part a { x;")
