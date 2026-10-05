"""Modifier/prefix model (issue #5) — derived from the inlined spec.

Answers, mechanically and for every usage/definition kind: *which modifier
or header tokens are legal, in what order?*  The model is computed from the
inlined language spec by walking the prefix-fragment chain for each node
kind, exactly as `classes.py`'s `RefPrefix.dump()` / `BasicUsagePrefix.dump()`
/ `OccurrenceUsagePrefix.dump()` were hand-derived.

Each modifier slot is:

    {
      "name": "isAbstract",            # canonical name (classes.py attr)
      "tokens": ["abstract"],          # the keyword(s) that produce it
      "kind": "flag" | "enum" | "ref",
      "cardinality": "?" | "*" | "+" | null,
      "mutually_exclusive_with": ["isVariation"],   # exclusion list
      "source_rule": "RefPrefix",      # fragment/rule that contributes it
      "source_line": 534,              # line in the .xtext
    }

The per-kind record also carries the chain that produced it, so reviewers
can trace the derivation back to the grammar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .inline import _import_order


class ModifierError(Exception):
    pass


# ---------------------------------------------------------------------------
# Traversal helpers over the element tree (same shape as inline.py)
# ---------------------------------------------------------------------------

def _walk(node: dict):
    """Yield every element dict in the tree (DFS, pre-order)."""
    if not isinstance(node, dict):
        return
    yield node
    kind = node.get("kind")
    if kind == "seq":
        for it in node.get("items", []):
            yield from _walk(it)
    elif kind == "alt":
        for ch in node.get("choices", []):
            yield from _walk(ch)
    elif kind == "group":
        if node.get("body") is not None:
            yield from _walk(node["body"])
    elif kind == "assign":
        if node.get("value") is not None:
            yield from _walk(node["value"])
    elif kind == "pred":
        if node.get("body") is not None:
            yield from _walk(node["body"])


def _flatten_seq_items(items: list[dict]) -> list[dict]:
    """Flatten nested seqs into a single list (keeps order)."""
    out: list[dict] = []
    for it in items:
        if it.get("kind") == "seq":
            out.extend(_flatten_seq_items(it.get("items", [])))
        else:
            out.append(it)
    return out


def _resolve_body(rule: dict, by_name: dict[str, dict], _seen: set[str] | None = None) -> dict | None:
    """Resolve a rule's body following `call` references to non-fragment
    rules (a rule body often IS a chain of other rules)."""
    _seen = _seen or set()
    body = rule.get("body")
    # follow a single call to its callee's body (chain resolution)
    if body is not None and body.get("kind") == "call":
        name = body.get("name")
        if name and name not in _seen and name in by_name:
            _seen.add(name)
            return _resolve_body(by_name[name], by_name, _seen)
    return body


# ---------------------------------------------------------------------------
# The core model builder
# ---------------------------------------------------------------------------

_KERNELS = [
    "UsageElement",         # usage-side kernel
    "DefinitionElement",    # definition-side kernel
    "Membership",           # member prefix
    "OccurrenceUsageElement",
    "NonOccurrenceUsageElement",
    "BehaviorUsageElement",
    "Definition",
    "Usage",
]


def _slot(name: str, tokens: list[str], kind: str, source_rule: str,
          source_line: int | None, card: str | None = None,
          mutually_exclusive_with: list[str] | None = None) -> dict:
    return {
        "name": name,
        "tokens": tokens,
        "kind": kind,
        "cardinality": card,
        "mutually_exclusive_with": mutually_exclusive_with or [],
        "source_rule": source_rule,
        "source_line": source_line,
    }


def _slots_from_assign(assign: dict, source_rule: str) -> dict | None:
    """Convert an ?=/= assign node into a modifier slot, when it is a flag/
    enum literal (not a child-reference assignment like ownedRelatedElement)."""
    name = assign.get("name", "")
    op = assign.get("op", "=")
    value = assign.get("value", {})
    vkind = value.get("kind")
    src_line = assign.get("line")

    # boolean flags: `isAbstract ?= 'abstract'`
    if op == "?=" and vkind == "lit":
        return _slot(name, [value.get("value", "").strip("'")], "flag",
                     source_rule, src_line, card=assign.get("card"),
                     mutually_exclusive_with=assign.get("mutually_exclusive_with", []))

    # enum-ish: `direction = FeatureDirection` (a single-valued keyword)
    if op == "=" and vkind == "call":
        return _slot(name, [value.get("name", "")], "ref", source_rule, src_line,
                     card=assign.get("card"))

    # enum with inline alternation: `portionKind = ( 'snapshot' | 'timeslice' )`
    if op == "=" and vkind == "group":
        inner = value.get("body", {})
        if inner.get("kind") == "alt":
            toks = [
                c.get("value", "").strip("'")
                for c in inner.get("choices", [])
                if c.get("kind") == "lit"
            ]
            return _slot(name, toks, "enum", source_rule, src_line,
                         card=assign.get("card"))
    return None


def _prefix_chain(rules: list[dict], start: str, by_name: dict[str, dict]):
    """Walk the prefix-fragment chain starting at rule `start`.

    Returns (ordered slot list, chain list of rule names).  Recursively
    resolves non-fragment `call` references and nested fragment calls,
    producing modifier slots in grammar/classes.py dump order.
    """
    chain: list[str] = []
    slots: list[dict] = []
    seen: set[str] = set()

    def rec(name: str):
        if name in seen:
            return
        seen.add(name)
        rule = by_name.get(name)
        if rule is None:
            return
        body = _resolve_body(rule, by_name)
        if body is None:
            return
        chain.append(name)
        # If the body is (or begins with) a call to another prefix fragment,
        # recurse into it first (outer prefix wraps inner, dump order is
        # outer-first too — RefPrefix items precede BasicUsagePrefix's 'ref').
        items = []
        bkind = body.get("kind")
        if bkind == "call":
            items = [body]
        elif bkind == "alt":
            # bare alternation of assigns (e.g. BasicDefinitionPrefix)
            choices = body.get("choices", [])
            assigns = [c for c in choices if c.get("kind") == "assign"]
            if assigns:
                names = [c.get("name") for c in assigns]
                for c in assigns:
                    sl = _slots_from_assign(c, name)
                    if sl is not None:
                        sl["mutually_exclusive_with"] = [
                            n2 for n2 in names if n2 != c.get("name")
                        ]
                        slots.append(sl)
            items = []
        elif bkind == "seq":
            items = _flatten_seq_items(body.get("items", []))
        else:
            items = [body]

        for el in items:
            ek = el.get("kind")
            if ek == "call":
                cname = el.get("name")
                if by_name.get(cname, {}).get("rule_kind") == "fragment":
                    rec(cname)
                else:
                    # a non-fragment call in a prefix position: resolve it
                    rec(cname)
            elif ek == "assign":
                sl = _slots_from_assign(el, name)
                if sl is not None:
                    # attach mutual exclusions discovered at this rule
                    slots.append(sl)
            elif ek == "lit" and name == "FeatureDirection":
                # FeatureDirection: in = 'in ' out = 'out' inout = 'inout'
                # handled by the fragment's body separately
                pass
            elif ek == "group":
                # a group may hold inner assigns (e.g. ( direction = FeatureDirection )?)
                gbody = el.get("body")
                gcard = el.get("card")
                # alternation group of assigns => mutually exclusive flags
                if gbody is not None and gbody.get("kind") == "alt":
                    choices = gbody.get("choices", [])
                    # collect assign slots from direct assigns AND seq choices
                    # (e.g. isOrdered ?= 'ordered' isNonunique ?= 'nonunique' | ...)
                    pick_assigns = []
                    for c in choices:
                        if c.get("kind") == "assign":
                            pick_assigns.append(c)
                        elif c.get("kind") == "seq":
                            for ci in c.get("items", []):
                                if ci.get("kind") == "assign":
                                    pick_assigns.append(ci)
                    if pick_assigns:
                        names = [c.get("name") for c in pick_assigns]
                        for c in pick_assigns:
                            sl = _slots_from_assign(c, name)
                            if sl is not None:
                                # inherit the group's optionality into the slot
                                if gcard and not sl.get("cardinality"):
                                    sl["cardinality"] = gcard
                                sl["mutually_exclusive_with"] = [
                                    n2 for n2 in names if n2 != c.get("name")
                                ]
                                slots.append(sl)
                        continue
                for inner in _walk(gbody):
                    if inner.get("kind") == "assign":
                        sl = _slots_from_assign(inner, name)
                        if sl is not None:
                            if gcard and not sl.get("cardinality"):
                                sl["cardinality"] = gcard
                            slots.append(sl)

    rec(start)
    return slots, chain


def build_modifier_model(spec: dict) -> dict:
    """Compute the per-kind modifier model from the spec.

    Walks the prefix-fragment chain for each known prefix, recording ordered
    modifier slots.  Uses the spec's rule table directly (fragments intact so
    the prefix chain — RefPrefix -> BasicUsagePrefix -> OccurrenceUsagePrefix —
    can be walked); the inlined form is not required for the chain because
    fragments are the very nodes being modeled.
    """
    rules = spec.get("rules", [])
    files = spec.get("files", [])
    by_name = {r["name"]: r for r in rules}
    # Per-grammar resolution (mirrors C1): build import_order once.
    import_order = _import_order(files)
    per_file: dict[str, dict[str, dict]] = {}
    for r in rules:
        f = r.get("source", {}).get("file")
        per_file.setdefault(f, {})[r["name"]] = r

    def scoped_by_name(start: str) -> dict[str, dict]:
        """Resolve names relative to the start rule's grammar (import chain)."""
        srule = by_name.get(start)
        if srule is None:
            return by_name
        cur = srule.get("source", {}).get("file")
        chain = import_order.get(cur, [cur])
        out = {}
        for f in chain:
            out.update(per_file.get(f, {}))
        # fallback: everything else (unscoped) last-wins
        for nm, r in by_name.items():
            out.setdefault(nm, r)
        return out

    # start kinds: all rules that are reachable from the kernels
    # (usage-side: rules whose body references the usage kernels; we take
    # every rule + fragment that appears in a prefix chain we know, plus
    # the explicitly named kinds from the issue).
    start_kinds = {
        "RefPrefix": "Usage",
        "BasicUsagePrefix": "Usage",
        "UsagePrefix": "Usage",
        "OccurrenceUsagePrefix": "OccurrenceUsage",
        "OccurrenceDefinitionPrefix": "OccurrenceDefinition",
        "BasicDefinitionPrefix": "Definition",
        "DefinitionPrefix": "Definition",
        "MemberPrefix": "Membership",
        "FeatureDirection": "FeatureDirection",
        "PortionKind": "PortionKind",
        "VisibilityIndicator": "VisibilityIndicator",
    }

    model: dict[str, dict] = {}
    for start, kind in start_kinds.items():
        local = scoped_by_name(start)
        slots, chain = _prefix_chain(rules, start, local)
        model[start] = {
            "kind": kind,
            "chain": chain,
            "slots": slots,
        }

    # Materialize enum-rule prefix kinds (FeatureDirection: in/out/inout)
    def enum_slots(rname: str, enum: dict) -> list[dict]:
        toks = enum.get("tokens", [])
        # tokens are [ident, '=', literal, '|', ident, '=', literal, ...]
        # keep only name/value pairs; '=' and '|' are separators.
        out = []
        i = 0
        while i + 2 < len(toks):
            nm_tok = toks[i]
            eq_tok = toks[i + 1]
            val_tok = toks[i + 2]
            if nm_tok.get("kind") == "ident" and eq_tok.get("value") == "=" \
                    and val_tok.get("kind") in ("literal", "ident"):
                nm = nm_tok.get("value")
                val = str(val_tok.get("value", "")).strip("'")
                others = [
                    t.get("value") for t in toks
                    if t.get("kind") == "ident" and t.get("value") != nm
                ]
                out.append(_slot(nm, [val], "enum", rname,
                                 nm_tok.get("line"),
                                 mutually_exclusive_with=others))
                i += 3
            else:
                i += 1
        return out
    # Add the enum-rule kinds (FeatureDirection, PortionKind, VisibilityIndicator)
    # as proper enum records in the model.
    for rname, r in by_name.items():
        if r.get("rule_kind") == "enum" and rname in start_kinds:
            sl = enum_slots(rname, r.get("body", {}))
            if sl:
                model[rname] = {"kind": "enum", "chain": [rname], "slots": sl}
    # Resolve cross-references: a ref slot whose callee is an enum rule in
    # the model (e.g. portionKind = PortionKind) becomes an enum slot with the
    # enum's real tokens, so the matrix shows snapshot/timeslice, not a bare
    # rule name.
    for rec in model.values():
        for s in rec["slots"]:
            if s["kind"] == "ref" and len(s["tokens"]) == 1:
                callee = s["tokens"][0]
                enum_rec = model.get(callee)
                if enum_rec and enum_rec.get("kind") == "enum":
                    s["kind"] = "enum"
                    s["tokens"] = [
                        ss["tokens"][0] for ss in enum_rec["slots"] if ss["tokens"]
                    ]
                    s["via"] = callee
    return model


def modifiers_for_kind(model: dict, kind: str) -> dict:
    """Return the modifier record for the given prefix/kind name."""
    key = kind if kind in model else None
    if key is None:
        # maybe the caller passed a node kind (PartUsage) — resolve the
        # prefix chain reachable from it
        for name, rec in model.items():
            if rec["kind"] == kind or name == kind:
                key = name
                break
    if key is None:
        raise ModifierError(f"no modifier record for {kind!r}")
    return model[key]


def generate_markdown_matrix(model: dict) -> str:
    """Human-readable Markdown matrix of the modifier model."""
    lines = ["# Modifier model — per node kind (issue #5)", ""]
    lines.append("| Prefix/kind | Modifier | Tokens | Kind | Card | Mutually-exclusive-with | Source |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for name in sorted(model):
        rec = model[name]
        for sl in rec["slots"]:
            lines.append(
                f"| {name} | {sl['name']} | {', '.join(sl['tokens'])} | "
                f"{sl['kind']} | {sl['cardinality'] or ''} | "
                f"{', '.join(sl['mutually_exclusive_with'])} | "
                f"{sl['source_rule']}:{sl['source_line']} |"
            )
        if not rec["slots"]:
            lines.append(f"| {name} | *(none)* | | | | |")
    return "\n".join(lines) + "\n"


def write_modifiers(model: dict, out_json: Path, out_md: Path | None = None) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        __import__("json").dumps(model, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if out_md is not None:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(generate_markdown_matrix(model), encoding="utf-8")
