"""Fragment inlining pass for the sysml2py-lab language spec.

Xtext semantics: a `fragment` is a reusable portion of a rule body; when a
rule references the fragment by name, the fragment's body is spliced in at
that point.  Assignments inside a fragment body are kept; a cardinality on
the reference call (`Frag*`) propagates onto the outermost spliced element.

This module applies that pass to the parsed `language_spec.json` so
downstream consumers (the `.tx` regenerator) see fully inlined rules —
matching the behavior textX expects in a `.tx` grammar (no fragments).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .spec import build_spec  # noqa: F401  (re-export convenience)


class FragmentError(Exception):
    pass


def _walk_element(el: dict, visit):
    """Depth-first walk of an element tree, in place."""
    visit(el)
    if el.get("kind") == "seq":
        for item in el.get("items", []):
            _walk_element(item, visit)
    elif el.get("kind") == "alt":
        for ch in el.get("choices", []):
            _walk_element(ch, visit)
    elif el.get("kind") == "group":
        if el.get("body") is not None:
            _walk_element(el["body"], visit)
    elif el.get("kind") == "assign":
        if el.get("value") is not None:
            _walk_element(el["value"], visit)
    elif el.get("kind") == "pred":
        if el.get("body") is not None:
            _walk_element(el["body"], visit)
    elif el.get("kind") == "xref":
        pass  # xref type/ref are names, not elements


def _deep(cl: dict) -> dict:
    return deepcopy(cl)


def _splice_cardinality(target: dict, card: str | None):
    """Propagate a cardinality from a fragment-call onto the spliced body."""
    if card is None:
        return
    # The outermost element of the fragment body gets the call's card.
    body = target
    # If the fragment body is a sequence, the card applies to the whole seq.
    if body.get("kind") == "seq":
        body = body
    body["card"] = card


def _inline_in_element(el: dict, by_name: dict[str, dict]):
    """Replace fragment calls within one element tree (in place)."""
    kind = el.get("kind")

    if kind == "call":
        name = el.get("name", "")
        frag = by_name.get(name)
        if frag is not None:
            body = frag["body"]
            card = el.get("card")
            # splice a deep copy of the fragment body in place of the call
            body_copy = _deep(body)
            if card is not None:
                _splice_cardinality(body_copy, card)
            # replace the call node's fields with the spliced body
            el.clear()
            el.update(body_copy)
            # the body may itself contain fragment calls -> recurse
            _inline_in_element(el, by_name)
        return

    if kind == "seq":
        items = el.get("items", [])
        for item in items:
            _inline_in_element(item, by_name)
    elif kind == "alt":
        for ch in el.get("choices", []):
            _inline_in_element(ch, by_name)
    elif kind == "group":
        if el.get("body") is not None:
            _inline_in_element(el["body"], by_name)
    elif kind == "assign":
        if el.get("value") is not None:
            _inline_in_element(el["value"], by_name)
    elif kind == "pred":
        if el.get("body") is not None:
            _inline_in_element(el["body"], by_name)


def inline_fragments(spec: dict) -> dict:
    """Inline all fragment references in the spec's rules.

    Returns a NEW spec dict with:
    - fragments removed from `rules` (they are expanded into hosts)
    - every fragment call replaced by a deep copy of the fragment body
    - `counts` recomputed (fragments=0)
    """
    rules = spec.get("rules", [])
    # build name -> rule index map (fragments only; first wins on dupes)
    frag_by_name: dict[str, dict] = {}
    non_frag: list[dict] = []
    for r in rules:
        if r.get("rule_kind") == "fragment":
            frag_by_name.setdefault(r["name"], r)
        else:
            non_frag.append(r)

    # inline into every non-fragment rule
    for r in non_frag:
        if r.get("body") is not None:
            _inline_in_element(r["body"], frag_by_name)

    # verify no unresolved fragment calls remain
    frag_names = set(frag_by_name)
    for r in non_frag:
        unresolved = []

        def _check(el):
            if el.get("kind") == "call" and el.get("name") in frag_names:
                unresolved.append(el["name"])

        _walk_element(r.get("body", {}), _check)
        if unresolved:
            raise FragmentError(
                f"rule {r['name']}: unresolved fragment calls {unresolved}"
            )

    # recompute counts
    counts = {
        "rules": sum(1 for r in non_frag if r["rule_kind"] == "rule"),
        "fragments": 0,
        "terminals": sum(1 for r in non_frag if r["rule_kind"] == "terminal"),
        "enums": sum(1 for r in non_frag if r["rule_kind"] == "enum"),
        "total": len(non_frag),
    }
    out = dict(spec)
    out["rules"] = non_frag
    out["counts"] = counts
    # files' rule_names no longer include fragments (they were absorbed)
    for f in out.get("files", []):
        f["rule_names"] = [r["name"] for r in out["rules"]
                           if r.get("source", {}).get("file") == f["file"]]
    return out
