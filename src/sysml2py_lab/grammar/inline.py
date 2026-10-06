"""Fragment inlining pass for the sysml2py-lab language spec.

Xtext semantics: a `fragment` is a reusable portion of a rule body; when a
rule references the fragment by name, the fragment's body is spliced in at
that point.  Assignments inside a fragment body are kept; a cardinality on
the reference call (`Frag*`) propagates onto the outermost spliced element.

Resolution is SCOPED PER GRAMMAR FILE, following the import chain
(KerMLExpressions -> KerML -> SysML): a fragment call inside a rule from
grammar G resolves to the fragment definition in G if present; otherwise
it walks up the import chain.  A bare global first-wins lookup is wrong
because 21 fragment names are defined in multiple grammars with different
bodies.

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
    target["card"] = card


def _import_order(files: list[dict]) -> dict[str, list[str]]:
    """Build a map source_file -> ordered import chain (self first).

    The spec's files carry grammar `with` chains as fully-qualified Xtext
    names (e.g. `org.omg.kerml.expressions.xtext.KerMLExpressions`); map
    those back to the source file name and build the transitive chain:
    SysML.xtext -> KerML.xtext -> KerMLExpressions.xtext.
    """
    # qualified grammar name -> source file name
    qname_to_file = {}
    short_to_file = {}
    for f in files:
        fname = f["file"]
        short_to_file[fname] = fname
        g = f.get("grammar", {})
        qname = g.get("name", "")
        if qname:
            qname_to_file[qname] = fname
            # also map by last dotted segment, e.g. "KerMLExpressions"
            short = qname.split(".")[-1]
            short_to_file[short] = fname
            short_to_file[short + ".xtext"] = fname

    def resolve(ref) -> str | None:
        if not ref:
            return None
        if isinstance(ref, str):
            if ref in short_to_file:
                return short_to_file[ref]
            if ref in qname_to_file:
                return qname_to_file[ref]
            return None
        return None

    chain: dict[str, list[str]] = {}
    names = [f["file"] for f in files]
    order_hint = ["KerMLExpressions.xtext", "KerML.xtext", "SysML.xtext"]
    for f in files:
        fname = f["file"]
        imports = []
        g = f.get("grammar", {})
        for ref in g.get("with", []) or []:
            imp = resolve(ref)
            if imp and imp not in imports:
                imports.append(imp)
        # transitive: what the imports import
        for imp in list(imports):
            if imp in chain:
                for t in chain[imp]:
                    if t not in imports and t != fname:
                        imports.append(t)
        chain[fname] = [fname] + imports
    # files with no recorded imports: use the standard stack when the files
    # are the real grammars; otherwise keep the file's own entry (synthetic
    # fixtures, e.g. T.xtext, must still resolve fragments within the file).
    for fname in names:
        if not chain.get(fname) or len(chain.get(fname, [])) == 1:
            if fname in order_hint:
                idx = order_hint.index(fname)
                chain[fname] = [fname] + [c for c in order_hint[idx + 1:] if c in names]
            elif any(c in names for c in order_hint):
                chain[fname] = [fname] + [c for c in order_hint if c in names]
            else:
                chain[fname] = [fname]
    # de-dup while preserving order
    for fname in chain:
        seen = set()
        out = []
        for x in chain[fname]:
            if x not in seen and x in names:
                seen.add(x)
                out.append(x)
        chain[fname] = out
    return chain


def _fragments_by_file(rules: list[dict]) -> dict[str, dict[str, dict]]:
    """Map source_file -> {fragment_name -> rule} (fragments only)."""
    out: dict[str, dict[str, dict]] = {}
    for r in rules:
        if r.get("rule_kind") != "fragment":
            continue
        f = r.get("source", {}).get("file")
        out.setdefault(f, {})[r["name"]] = r
    return out


def _inline_in_element(el: dict, by_file: dict[str, dict[str, dict]],
                       import_order: dict[str, list[str]], current_file: str,
                       _seen: set[str] | None = None):
    """Replace fragment calls within one element tree (in place)."""
    _seen = _seen or set()
    kind = el.get("kind")

    if kind == "call":
        name = el.get("name", "")
        frag = _resolve_fragment(name, current_file, by_file, import_order, _seen)
        if frag is not None:
            body = frag["body"]
            card = el.get("card")
            # splice a deep copy of the fragment body in place of the call
            body_copy = _deep(body)
            # C3: wrap alt-rooted bodies in a group so the spliced | does not
            # fuse with host alternation; same for any carded body.
            if body_copy.get("kind") == "alt" or card is not None:
                wrapper = {"kind": "group", "body": body_copy}
                if card is not None:
                    wrapper["card"] = card
                body_copy = wrapper
                card = None  # already applied on wrapper
            if card is not None:
                _splice_cardinality(body_copy, card)
            # replace the call node's fields with the spliced body
            el.clear()
            el.update(body_copy)
            # the body may itself contain fragment calls -> recurse
            _inline_in_element(el, by_file, import_order, current_file, _seen)
        return

    if kind == "seq":
        items = el.get("items", [])
        for item in items:
            _inline_in_element(item, by_file, import_order, current_file, _seen)
    elif kind == "alt":
        for ch in el.get("choices", []):
            _inline_in_element(ch, by_file, import_order, current_file, _seen)
    elif kind == "group":
        if el.get("body") is not None:
            _inline_in_element(el["body"], by_file, import_order, current_file, _seen)
    elif kind == "assign":
        if el.get("value") is not None:
            _inline_in_element(el["value"], by_file, import_order, current_file, _seen)
    elif kind == "pred":
        if el.get("body") is not None:
            _inline_in_element(el["body"], by_file, import_order, current_file, _seen)


def _resolve_fragment(name: str, current_file: str,
                      by_file: dict[str, dict[str, dict]],
                      import_order: dict[str, list[str]],
                      seen: set[str]) -> dict | None:
    """Resolve a fragment name in the context of current_file's grammar.

    Look in current file first, then walk up the import chain.  Guard against
    infinite recursion (mutually recursive fragments).
    """
    if name in seen:
        raise FragmentError(f"cyclic fragment reference on {name!r}")
    seen = set(seen)
    seen.add(name)
    chain = import_order.get(current_file, [current_file])
    for f in chain:
        frag = by_file.get(f, {}).get(name)
        if frag is not None:
            return frag
    return None


def inline_fragments(spec: dict) -> dict:
    """Inline all fragment references in the spec's rules.

    Returns a NEW spec dict with:
    - fragments removed from `rules` (they are expanded into hosts)
    - every fragment call replaced by a deep copy of the fragment body
    - `counts` recomputed (fragments=0)
    - a deep copy of the input (no aliasing; caller's spec untouched)
    """
    rules = spec.get("rules", [])
    # deep-copy up-front so the caller's spec is never mutated
    rules = deepcopy(rules)

    by_file = _fragments_by_file(rules)
    import_order = _import_order(spec.get("files", []))

    non_frag: list[dict] = [r for r in rules if r.get("rule_kind") != "fragment"]

    # inline into every non-fragment rule
    for r in non_frag:
        if r.get("body") is not None:
            cur = r.get("source", {}).get("file")
            _inline_in_element(r["body"], by_file, import_order, cur or "")

    # verify no unresolved fragment calls remain
    frag_names = {n for frags in by_file.values() for n in frags}
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
