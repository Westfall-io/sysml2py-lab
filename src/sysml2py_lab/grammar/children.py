"""Children / body-membership model (issue #6) — derived from the spec.

Answers, mechanically: *what may appear inside each body, and wrapped in
which membership node?*  The model is computed from the language spec by
walking container `*Body` rules -> `*BodyItem` alternations -> membership
wrappers -> element-capability rules -> concrete element kinds, matching how
the current sysml2py `usage.py` builders hand-assemble membership chains.

Each child entry records:

    {
      "wrapper": "OccurrenceUsageMember",   # the membership node
      "relationship": "ownedRelationship",  # how the wrapper attaches
      "chain": ["OccurrenceUsageMember", "OccurrenceUsageElement",
                "StructureUsageElement"],   # wrapper -> element resolution
                                           # trail (NOT a strict linear path:
                                           # a capability alt's branches are
                                           # siblings; the trail lists them
                                           # in source order)
      "kinds": ["PartUsage", "ItemUsage", ...],  # concrete element kinds
      "cardinality": "*" | "?" | "+" | null,
      "prefixes": ["EmptySuccessionMember"],   # ordering/succession prefixes
      "source_rule": "DefinitionBodyItem",
      "source_line": 490,
    }

The model is the codegen input for the generated builder API: it says which
children a part/requirement/package may hold and under which membership
wrapper.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ChildrenError(Exception):
    pass


# ---------------------------------------------------------------------------
# Traversal helpers
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


def _walk_cards(node: dict, inherited: str | None = None):
    """Yield (element, inherited_card) depth-first, where inherited_card is
    the repetition marker of the nearest enclosing group/pred that repeats
    its content (C6).

    The grammar attaches repetition to the *enclosing* node, e.g.
        '{' ( owned+=A | owned+=B )* '}'   <- the '*' is on the group, not A/B
        '{' BodyItem* '}'                  <- '*' is on the BodyItem call
        CalculationBodyPart: [=> BodyItem* (owned+=R)?]  <- '*' inside the Part
    Reading only the member's own card misses the enclosing marker.  Carry
    the group/pred repeat down: a group/pred's '*'/'?'/'+' means its content
    (every member inside) repeats.  A seq/alt's card repeats the *whole*
    production (not each child), so it is NOT inherited by children.
    """
    if not isinstance(node, dict):
        return
    kind = node.get("kind")
    card = node.get("card") or inherited if kind in ("group", "pred") else inherited
    yield node, card
    if kind == "seq":
        for it in node.get("items", []):
            yield from _walk_cards(it, card)
    elif kind == "alt":
        for ch in node.get("choices", []):
            yield from _walk_cards(ch, card)
    elif kind == "group":
        if node.get("body") is not None:
            yield from _walk_cards(node["body"], card)
    elif kind == "assign":
        if node.get("value") is not None:
            yield from _walk_cards(node["value"], card)
    elif kind == "pred":
        if node.get("body") is not None:
            yield from _walk_cards(node["body"], card)


def _collect_calls(el: dict, acc: list[str]):
    """Collect every call target name in the tree (dedup'd, ordered)."""
    if not isinstance(el, dict):
        return
    k = el.get("kind")
    if k == "call" and el.get("name"):
        acc.append(el["name"])
    for key in ("value", "body"):
        v = el.get(key)
        if isinstance(v, dict):
            _collect_calls(v, acc)
    for key in ("items", "choices"):
        for x in el.get(key, []) or []:
            _collect_calls(x, acc)


def _simplify(el: dict) -> str:
    """Render a body to a compact string for debugging/provenance."""
    if not isinstance(el, dict):
        return str(el)
    k = el.get("kind")
    if k == "call":
        return el.get("name", "") + (el.get("card") or "")
    if k == "lit":
        return repr(el.get("value"))
    if k == "assign":
        val = el.get("value")
        return el.get("name", "") + el.get("op", "=") + (_simplify(val) if isinstance(val, dict) else str(val))
    if k == "seq":
        return "(" + " ".join(_simplify(x) for x in el.get("items", [])) + ")"
    if k == "alt":
        return "|".join(_simplify(x) for x in el.get("choices", []))
    if k == "group":
        gb = el.get("body")
        return "(" + (_simplify(gb) if isinstance(gb, dict) else str(gb)) + (el.get("card") or "") + ")"
    if k == "pred":
        pb = el.get("body")
        return "=>" + (_simplify(pb) if isinstance(pb, dict) else str(pb))
    return k


# ---------------------------------------------------------------------------
# The core model builder
# ---------------------------------------------------------------------------

#: Container body rules (issue #6 scope): name -> the body-item rule(s) it
#: permits.  Discovered mechanically below; this only names the containers
#: to report (a *Body rule whose body references *BodyItem/Member rules).
_CONTAINER_SUFFIX = "Body"


def _is_container(rule: dict) -> bool:
    """A container is a rule ending in 'Body' whose body references item or
    member rules (i.e. it can hold children)."""
    name = rule.get("name", "")
    if not name.endswith(_CONTAINER_SUFFIX):
        return False
    if rule.get("rule_kind") not in ("rule", "fragment"):
        return False
    body = rule.get("body")
    if body is None:
        return False
    calls: list[str] = []
    _collect_calls(body, calls)
    return any(c.endswith("Member") or c.endswith("BodyItem") or c.endswith("BodyPart")
               for c in calls)


def _resolve_rule(name: str, by_name: dict[str, dict], seen: set[str] | None = None) -> dict | None:
    """Follow a rule's body: if it is a single call to another rule, chase it
    down to the first non-call body (or the call is to a fragment we can
    inline)."""
    seen = seen or set()
    if name in seen:
        return None
    seen.add(name)
    rule = by_name.get(name)
    if rule is None:
        return None
    body = rule.get("body")
    if body is not None and body.get("kind") == "call":
        inner = by_name.get(body.get("name", ""))
        if inner is not None and body.get("name") not in seen:
            return _resolve_rule(body["name"], by_name, seen)
    return rule


def _element_kinds(call_name: str, by_name: dict[str, dict], seen: set[str] | None = None,
                   depth: int = 0) -> list[str]:
    """Resolve an element-capability rule down to the concrete element kinds.

    A rule is a *capability/wrapper node* worth expanding when it is either:
      * a member/capability rule (name ends in 'Member'/'Element' — e.g.
        `OccurrenceUsageElement`, `NonFeatureMember`), or
      * an alt-of-calls dispatch (e.g. `FeatureMember` ->
        `TypeFeatureMember | OwnedFeatureMember`).
    Everything else is a concrete child kind (e.g. `PartUsage`, `Package`,
    `TransitionUsage`, `EnumeratedValue`) — return its name and stop.
    """
    if depth > 12:
        return [call_name]
    seen = seen or set()
    if call_name in seen:
        return [call_name]
    rule = by_name.get(call_name)
    if rule is None:
        return [call_name]
    body = rule.get("body")
    if body is None:
        return [call_name]
    k = body.get("kind")

    is_dispatch = (k == "alt" and body.get("choices")
                   and all(c.get("kind") == "call" for c in body.get("choices", [])))
    is_wrapper = call_name.endswith("Member") or call_name.endswith("Element")
    if not (is_wrapper or is_dispatch):
        # a concrete kind (or a plain rule) — itself is the answer
        return [call_name]

    # capability node: flatten its branches
    out: list[str] = []
    if k == "alt":
        for ch in body.get("choices", []):
            if ch.get("kind") == "call":
                out.extend(_element_kinds(ch["name"], by_name, seen | {call_name}, depth + 1))
            elif ch.get("kind") == "seq":
                for inner in ch.get("items", []):
                    if inner.get("kind") == "call":
                        out.extend(_element_kinds(inner["name"], by_name, seen | {call_name}, depth + 1))
                        break
    elif k == "seq":
        for inner in _walk(body):
            if inner.get("kind") == "assign" and inner.get("name", "").startswith("owned"):
                v = inner.get("value")
                if v is not None and v.get("kind") == "call":
                    out.extend(_element_kinds(v["name"], by_name, seen | {call_name}, depth + 1))
                break
    elif k == "assign":
        v = body.get("value")
        if v is not None and v.get("kind") == "call":
            out.extend(_element_kinds(v["name"], by_name, seen | {call_name}, depth + 1))
    # a wrapper with no owned call target (e.g. AliasMember: memberElement=xref,
    # Expose: group-alt + RelationshipBody) is itself the concrete kind
    if not out:
        return [call_name]
    return sorted(set(out))


def _expand_bodyitem(bodyitem_el: dict, by_name: dict[str, dict], parent_card: str | None = None) -> list[dict]:
    """Expand a bare `*BodyItem`/`*BodyPart` call into the member
    alternatives it permits, as separate child entries.

    A `*BodyPart` (e.g. `StateBodyPart`) is a thin wrapper: `=> BodyItem` or
    a seq of `=> BodyItem` + extra members — chase it to the underlying
    `*BodyItem` (or, for `FunctionBodyPart`, straight to the member set).
    Handles direct member assigns, seq choices (an optional succession
    prefix + a member), and pred-guarded choices (=> Import).

    `parent_card` is the repetition marker attached by the container's
    calling site (usually '*' on the BodyItem call) — propagated to each
    entry as its cardinality when the member itself has no explicit card.
    """
    bname = bodyitem_el.get("name", "")
    cur = bname
    # chase *BodyPart -> *BodyItem (or straight to members for BodyParts that
    # hold members directly, e.g. FunctionBodyPart).  A *BodyPart may ALSO
    # carry its own member assigns beside the delegated *BodyItem (e.g.
    # CalculationBodyPart: [=> CalculationBodyItem* (ownedRelationship +=
    # ResultExpressionMember)?]) — collect those part-level members too (C5).
    part_members: list[dict] = []
    part_member_cards: list[str | None] = []  # parallel to part_members (W13:
    # no _card stamps into the shared spec — carry cards separately)
    part_rule: dict | None = None  # N3: the declaring *BodyPart for provenance
    # C6: a *BodyPart often carries no card itself; the repetition lives on
    # the *BodyItem *inside* it (CalculationBodyPart: [=> BodyItem* ...]).
    # Thread that inner card out to the expansion so members inherit '*'.
    part_item_card: str | None = None
    while cur.endswith("BodyPart"):
        pr = by_name.get(cur)
        if pr is None:
            return []
        part_rule = pr
        pbody = pr.get("body")
        nxt = None
        if pbody is not None:
            # use _walk_cards so the group's own '*' (FunctionBodyPart's
            # group(alt(members))[*]) flows to the member assigns (C6)
            for el, inh in _walk_cards(pbody):
                if el.get("kind") == "call" and el["name"].endswith("BodyItem"):
                    nxt = el["name"]
                    part_item_card = el.get("card") or inh
                elif el.get("kind") == "assign" and el.get("name", "").startswith("owned"):
                    v = el.get("value")
                    if v is not None and v.get("kind") == "call" and v["name"] != "EmptySuccessionMember":
                        # carry the enclosing group's card alongside, not onto
                        # the element (avoid mutating the shared spec dict)
                        part_members.append(el)
                        part_member_cards.append(inh or el.get("card"))
        if not nxt:
            break
        cur = nxt
    brule = by_name.get(cur)
    if brule is None:
        return []
    body = brule.get("body")
    if body is None:
        return []
    out = []
    # The resolved item rule body is usually an `alt` of member choices
    # (e.g. DefinitionBodyItem).  For a BodyPart that holds members directly
    # (FunctionBodyPart: [group{...}*, group(ResultExpressionMember)?]) the
    # body is a `seq` — walk its items for the same member choices.
    # (W13) collect (choice, card) pairs so we never mutate the shared spec:
    choices: list[tuple[dict, str | None]] = []
    if body.get("kind") == "alt":
        choices = [(c, c.get("card")) for c in body.get("choices", [])]
    elif body.get("kind") == "seq":
        for it in body.get("items", []):
            if it.get("kind") == "group":
                gb = it.get("body")
                if gb is not None and gb.get("kind") == "alt":
                    # C6: the group carries the repetition (group(alt(members))[*])
                    group_card = it.get("card")
                    for gc in gb.get("choices", []):
                        choices.append((gc, gc.get("card") or group_card))
                elif gb is not None and gb.get("kind") == "assign":
                    choices.append((gb, gb.get("card") or it.get("card")))
            elif it.get("kind") == "assign":
                choices.append((it, it.get("card")))
    if not choices:
        return []
    # C6: prefer the *BodyPart's inner *BodyItem card ('*') as the default
    # repetition for members of the delegated item rule.
    eff_parent = part_item_card or parent_card
    for ch, ch_card in choices:
        chk = ch.get("kind")
        if chk == "assign" and ch.get("name", "").startswith("owned"):
            # direct member (may also carry succession prefix info)
            sub = _item_entry(ch, by_name, brule, parent_card=ch_card or eff_parent)
            out.append(sub)
        elif chk == "call" and ch.get("name", "").endswith("BodyItem"):
            # delegation to a base body (e.g.
            # RequirementBodyItem: DefinitionBodyItem | SubjectMember | ...)
            # — inherit the base's members
            out.extend(_expand_bodyitem(ch, by_name, eff_parent))
        elif chk == "seq":
            # seq branch: a succession of member assignments, with an
            # optional EmptySuccessionMember prefix on the FIRST member, and
            # possible pred-guarded repeats (e.g.
            #   [group(EmptySuccessionMember)?, assign(owned+=StructureUsageMember)]
            #   [assign(owned+=InitialNodeMember),
            #     group(pred=>assign(owned+=TargetSuccessionMember))*]
            #   [group(EmptySuccessionMember)?,
            #     assign(owned+=group(alt(BehaviorUsageMember|ActionNodeMember)))]
            # C5: collect EVERY owned member assign (do not overwrite); a
            # group-of-alts value yields one entry per alt branch; a prefix
            # attaches to the member it syntactically precedes.
            prefix_queue = []
            for inner in ch.get("items", []):
                for a in _walk(inner):
                    if a.get("kind") != "assign" or not a.get("name", "").startswith("owned"):
                        continue
                    v = a.get("value")
                    if v is None:
                        continue
                    if v.get("kind") == "call":
                        vname = v["name"]
                        if vname == "EmptySuccessionMember":
                            prefix_queue.append(vname)
                            continue
                        if vname.endswith("Member") or vname.endswith("BodyItem"):
                            sub = _item_entry(
                                a, by_name, brule,
                                parent_card=a.get("card") or v.get("card") or eff_parent)
                            sub["prefixes"] = sorted(set(sub.get("prefixes", [])) | set(prefix_queue))
                            out.append(sub)
                            prefix_queue = []
                    elif v.get("kind") == "group":
                        gb = v.get("body")
                        gname = None
                        if isinstance(gb, dict) and gb.get("kind") == "alt":
                            # group(alt(call1 | call2)): one entry per branch
                            for gc in gb.get("choices", []):
                                if gc.get("kind") == "call" and gc["name"].endswith("Member"):
                                    synth = dict(gc)
                                    synth["card"] = a.get("card") or gb.get("card")
                                    sub = _item_entry(
                                        {"kind": "assign", "name": a["name"], "op": a.get("op", "+="),
                                         "value": synth},
                                        by_name, brule,
                                        parent_card=a.get("card") or gb.get("card") or eff_parent)
                                    sub["prefixes"] = sorted(set(sub.get("prefixes", [])) | set(prefix_queue))
                                    out.append(sub)
                            prefix_queue = []
                        elif isinstance(gb, dict) and gb.get("kind") == "call" and gb["name"].endswith("Member"):
                            sub = _item_entry(
                                a, by_name, brule,
                                parent_card=a.get("card") or gb.get("card") or eff_parent)
                            sub["prefixes"] = sorted(set(sub.get("prefixes", [])) | set(prefix_queue))
                            out.append(sub)
                            prefix_queue = []
        elif chk == "pred":
            # pred => assign(owned+=call(Import))
            pb = ch.get("body")
            if pb is not None:
                for a in _walk(pb):
                    if a.get("kind") == "assign" and a.get("name", "").startswith("owned"):
                        v = a.get("value")
                        if v is not None and v.get("kind") == "call":
                            sub = _item_entry(a, by_name, brule, parent_card=parent_card)
                            out.append(sub)
    # C5: also expand the *BodyPart's own member assigns (e.g.
    # ResultExpressionMember in CalculationBodyPart) unless already present.
    # (N3) their provenance is the declaring *BodyPart rule, not the resolved
    # *BodyItem rule.
    have = {e["wrapper"] for e in out}
    for pm, pmc in zip(part_members, part_member_cards):
        sub = _item_entry(pm, by_name, part_rule or brule,
                          parent_card=pmc or part_item_card or parent_card)
        if sub["wrapper"] not in have:
            out.append(sub)
            have.add(sub["wrapper"])
    return out


def _item_entry(item_el: dict, by_name: dict[str, dict], owner_rule: dict | None = None,
                parent_card: str | None = None) -> dict:
    # The choice element is typically:
    #   assign(ownedRelationship+=call(MemberRule))         (card may be *)
    #   pred => assign(ownedRelationship+=call(MemberRule))
    #   seq[group(assign(ownedRelationship+=call(SuccessionPrefix))?),
    #       assign(ownedRelationship+=call(MemberRule))]
    # Extract: the member wrapper call + its card + succession prefixes.
    wrapper_call = None
    wrapper_card = None
    prefixes: list[str] = []
    relationship = "ownedRelationship"  # body-item attach level

    def scan(el: dict):
        nonlocal wrapper_call, wrapper_card
        if not isinstance(el, dict):
            return
        k = el.get("kind")
        if k == "assign":
            name = el.get("name", "")
            value = el.get("value", {})
            if name.startswith("owned") and value.get("kind") == "call" \
                    and value.get("name", "").endswith("Member"):
                if wrapper_call is None:
                    wrapper_call = value["name"]
                    wrapper_card = el.get("card") or value.get("card")
            elif name.startswith("owned") and value.get("kind") == "call":
                # a non-Member owned assignment: treat as a child relationship
                # (e.g. Import) with no wrapper chain
                if wrapper_call is None:
                    wrapper_call = value["name"]
                    wrapper_card = el.get("card") or value.get("card")
            elif value.get("kind") == "call" and value["name"].endswith("Member"):
                if wrapper_call is None:
                    wrapper_call = value["name"]
                    wrapper_card = el.get("card") or value.get("card")
        elif k == "call":
            if el.get("name", "").endswith("Member") and wrapper_call is None:
                wrapper_call = el["name"]
                wrapper_card = el.get("card")
            elif el.get("name", "").endswith("Member") and el["name"] != wrapper_call:
                prefixes.append(el["name"])
        for key in ("value", "body"):
            v = el.get(key)
            if isinstance(v, dict):
                scan(v)
        for key in ("items", "choices"):
            for x in el.get(key, []) or []:
                scan(x)

    scan(item_el)

    if wrapper_call is None:
        # fall back to the item rule's first owned-relationship member call
        calls: list[str] = []
        _collect_calls((owner_rule or {}).get("body", {}), calls)
        for c in calls:
            if c.endswith("Member"):
                wrapper_call = c
                break

    # W9: never emit a None wrapper (would crash the markdown matrix join).
    # If resolution genuinely finds no member, fall back to a stable name so
    # the entry is still usable; kinds will resolve to that name as a leaf.
    if wrapper_call is None:
        fallback = (item_el.get("value") or {}).get("name") \
            if isinstance(item_el.get("value"), dict) else None
        wrapper_call = fallback or "(unnamed-member)"

    # C4: the member card may legitimately be None when the repetition
    # marker lives on the enclosing group / BodyItem call (e.g.
    # group{...}* ownedRelationship+=AnnotatingMember).  Fall back to the
    # caller-provided parent card.
    if wrapper_card is None and parent_card:
        wrapper_card = parent_card

    # chain resolution: the wrapper -> element capability -> concrete kinds
    chain = [wrapper_call]
    seen = set()
    cur = wrapper_call
    resolved_kinds: list[str] = []
    while cur and cur not in seen:
        seen.add(cur)
        rule = by_name.get(cur)
        if rule is None:
            break
        body = rule.get("body")
        if body is None:
            break
        k = body.get("kind")
        # Follow one hop from a wrapper into its element target.  Returns
        # ("descend", name) when the target is itself a member/capability
        # rule, ("kind", name) when the target is a concrete element (its
        # name IS the child kind), or None when there is no owned target.
        def next_hop(rbody: dict) -> tuple[str, str] | None:
            if rbody.get("kind") == "seq":
                for inner in _walk(rbody):
                    if inner.get("kind") == "assign" and inner.get("name", "").startswith("owned"):
                        v = inner.get("value")
                        if v is not None and v.get("kind") == "call":
                            return _hop(v["name"])
                return None
            if rbody.get("kind") == "assign":
                v = rbody.get("value")
                if v is not None and v.get("kind") == "call":
                    return _hop(v["name"])
            return None

        def _hop(nxt: str) -> tuple[str, str]:
            if nxt.endswith("Member") or nxt.endswith("Element"):
                return ("descend", nxt)
            return ("kind", nxt)

        moved = False
        hop = next_hop(body)
        if hop is not None:
            verb, nxt = hop
            if verb == "descend" and nxt not in seen:
                chain.append(nxt)
                cur = nxt
                moved = True
            elif verb == "kind":
                # concrete element: its name is the child kind — stop
                resolved_kinds = [nxt]
                break
        if not moved:
            # an alt body (or a rule with no owned hop): this is the element
            # capability that decides the child kinds
            if k == "alt":
                kinds = _element_kinds(cur, by_name, set())
                resolved_kinds = kinds
                # extend the chain with sub-capability rule names (e.g. an
                # OccurrenceUsageElement whose alt branches are the
                # StructureUsageElement / BehaviorUsageElement capabilities)
                for ch in body.get("choices", []):
                    if ch.get("kind") == "call" and ch["name"].endswith("Element"):
                        if ch["name"] not in chain:
                            chain.append(ch["name"])
            break
        continue

    # resolved_kinds may be empty if we stopped at a call to an element
    # rule whose body is an alt (we set cur to it); fall through to resolve.
    if not resolved_kinds and cur:
        resolved_kinds = _element_kinds(cur, by_name, set())

    src = (owner_rule or {}).get("source", {})
    return {
        "wrapper": wrapper_call,
        "relationship": relationship,
        "chain": chain,
        "kinds": resolved_kinds,
        "cardinality": wrapper_card,
        "prefixes": sorted(set(prefixes)),
        "source_rule": (owner_rule or {}).get("name", ""),
        "source_line": src.get("line"),
    }


def build_children_model(spec: dict) -> dict:
    """Compute the per-container children model from the spec."""
    rules = spec.get("rules", [])
    files = spec.get("files", [])
    by_name = {r["name"]: r for r in rules}

    # C1: group containers by NAME (KerML and SysML define the same rule
    # names); `by_name` keeps the last definition (SysML wins, matching the
    # override direction).  Without grouping, each same-name rule would append
    # duplicate entries into the same body list.
    containers = [r for r in rules if _is_container(r)]
    by_container: dict[str, list[dict]] = {}
    for cont in sorted(containers, key=lambda r: r["name"]):
        by_container.setdefault(cont["name"], []).append(cont)

    bodies: dict[str, list[dict]] = {}
    for name in sorted(by_container):
        # C7: the correct rule for a container name is the LAST definition in
        # by_name (the declared override winner — SysML overrides KerML,
        # which overrides KerMLExpressions).  `_is_container` must be
        # evaluated on THIS rule, not on the pre-filtered `containers` list:
        # the SysML ExpressionBody override is `call(CalculationBody)`, which
        # contains no Member/BodyItem/BodyPart call and so was filtered out
        # of `containers`, leaving the superseded KerMLExpressions default.
        cont = by_name.get(name)
        if cont is None:
            continue
        body = cont.get("body")
        if body is None:
            continue
        # follow single-call aliases (ExpressionBody -> CalculationBody),
        # mirroring the existing UsageBody -> DefinitionBody alias handling:
        # a container whose winning body is `call(X)` inherits X's container
        # behavior; only a true alias (X not itself named *Body) is dropped.
        while body is not None and body.get("kind") == "call":
            target = body.get("name")
            if not target:
                break
            if target in by_container or target.endswith(_CONTAINER_SUFFIX):
                # alias to a real container: reuse its entries
                alias_body = by_name.get(target)
                body = alias_body.get("body") if alias_body else None
                break
            # non-container alias (UsageBody -> DefinitionBody): omit
            break
        if body is None:
            continue
        entries: list[dict] = []
        # container body is usually alt{ ';' | '{' BodyItem* '}' } or
        # seq[lit('{'), BodyItem*, lit('}')]; gather every assign-of-member
        # call and its card from the tree, plus pred-guarded imports.
        candidate_els = []
        for el, inh in _walk_cards(body):
            # assigned member (ownedRelationship+=call(Member)); inherit the
            # enclosing group/pred card (C6: '{' (A|B)* '}')
            if el.get("kind") == "assign" and el.get("name", "").startswith("owned"):
                candidate_els.append((el, inh))
            # bare body-item call (DefinitionBodyItem[*], StateBodyPart) —
            # expand below.  Keep its card so the BodyItem repetition (which
            # the grammar attaches to the enclosing '*' group) is preserved.
            elif el.get("kind") == "call" and (
                el.get("name", "").endswith("BodyItem")
                or el.get("name", "").endswith("BodyPart")
            ):
                # for a BodyItem call, its own '*' is the repetition marker
                candidate_els.append((el, el.get("card") or inh))
        seen_wrappers = set()
        for el, inh in candidate_els:
            if el.get("kind") == "call" and (
                el.get("name", "").endswith("BodyItem")
                or el.get("name", "").endswith("BodyPart")
            ):
                for sub in _expand_bodyitem(el, by_name, inh):
                    w = sub.get("wrapper")
                    key = (w, sub.get("cardinality"))
                    if key not in seen_wrappers:
                        bodies.setdefault(cont["name"], []).append(sub)
                        seen_wrappers.add(key)
                continue
            v = el.get("value")
            if v is None or v.get("kind") != "call":
                continue
            wrapper = v.get("name", "")
            entry = _item_entry(el, by_name, by_name.get(wrapper, {}),
                                inh or el.get("card"))
            # the entry's item rule is the *call target* (the member), not
            # this container's rule; fix source provenance to the member rule
            mrule = by_name.get(wrapper) or {"name": wrapper, "source": {}}
            entry["source_rule"] = wrapper
            entry["source_line"] = mrule.get("source", {}).get("line")
            # C5 knock-on 2: dedupe on (wrapper, cardinality, prefixes) so a
            # distinct prefixed variant is not dropped (ActionBodyItem's
            # TargetSuccessionMember has an EmptySuccessionMember-prefixed
            # branch AND a plain branch).
            key = (wrapper, entry["cardinality"], tuple(sorted(entry["prefixes"])))
            if key not in seen_wrappers:
                bodies.setdefault(cont["name"], []).append(entry)
                seen_wrappers.add(key)

    # Build the parity report comparing the model's definition kinds against
    # the sibling classes.py DefinitionElement dispatch.
    parity = _build_parity_report(bodies, by_name, rules)
    return {"bodies": bodies, "parity_report": parity}


# ---------------------------------------------------------------------------
# Parity report (classes.py comparison)
# ---------------------------------------------------------------------------

#: The 5 definition kinds named in issue #6 as present in the grammar's
#: DefinitionElement but absent from classes.py's DefinitionElement dispatch.
_ISSUE_MISSING_DEFINITIONS = [
    "UseCaseDefinition",
    "OccurrenceDefinition",
    "ViewDefinition",
    "VerificationCaseDefinition",
    "MetadataDefinition",
]

#: classes.py DefinitionElement dispatch (verified by inspection, lines
#: 106-173): the kinds it handles.
_CLASSES_DEFINITION_DISPATCH = [
    "Package",
    "PartDefinition",
    "AttributeDefinition",
    "AnnotatingElement",
    "EnumerationDefinition",
    "ItemDefinition",
    "ConnectionDefinition",
    "PortDefinition",
    "InterfaceDefinition",
    "FlowConnectionDefinition",
    "ActionDefinition",
    "CalculationDefinition",
    "StateDefinition",
    "ConstraintDefinition",
    "RequirementDefinition",
    "AnalysisCaseDefinition",
]


def _grammar_definition_kinds(rules: list[dict]) -> list[str]:
    """The concrete kinds the grammar's DefinitionElement can produce."""
    for r in rules:
        if r.get("name") == "DefinitionElement":
            body = r.get("body")
            if body is not None and body.get("kind") == "alt":
                return sorted(
                    ch.get("name") for ch in body.get("choices", [])
                    if ch.get("kind") == "call"
                )
    return []


def _build_parity_report(bodies: dict[str, list[dict]], by_name: dict[str, dict],
                         rules: list[dict]) -> str:
    """Render the classes.py parity report (issue #6 deliverable)."""
    grammar_defs = _grammar_definition_kinds(rules)
    missing_in_classes = sorted(
        k for k in grammar_defs if k not in _CLASSES_DEFINITION_DISPATCH
    )
    modelled = sorted({k for body in bodies.values() for e in body for k in e["kinds"]})

    lines = [
        "# classes.py parity report (issue #6)",
        "",
        "Compares the grammar-derived children/body model against the current",
        "hand-curated `sysml2py/grammar/classes.py` (266 classes, 265 `dump()`,",
        "72 `get_definition()`, 73 `NotImplementedError` sites).",
        "",
        "## Missing definition kinds (implemented-but-unmodelled)",
        "",
        "The grammar's `DefinitionElement` allows these kinds, but `classes.py`'s",
        "`DefinitionElement` dispatch does not handle them:",
        "",
        "| Kind | Class in classes.py |",
        "| --- | --- |",
    ]
    for k in sorted(grammar_defs):
        in_classes = "yes" if k in _CLASSES_DEFINITION_DISPATCH else "**NO**"
        lines.append(f"| {k} | {in_classes} |")

    lines += [
        "",
        "### Issue-mandated missing kinds",
        "",
        "| Missing definition kind | In classes.py dispatch? |",
        "| --- | --- |",
    ]
    for k in _ISSUE_MISSING_DEFINITIONS:
        ok = "**MISSING**" if k not in _CLASSES_DEFINITION_DISPATCH else "present"
        lines.append(f"| {k} | {ok} |")

    lines += [
        "",
        "## Modelled kinds (this model's child-kind vocabulary)",
        "",
        f"{len(modelled)} distinct element kinds are modeled across all bodies:",
        "",
        "```",
        " ".join(modelled),
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Query API + writers
# ---------------------------------------------------------------------------

def children_for_body(model: dict, body_name: str) -> list[dict]:
    """Return the child entries for a body container."""
    bodies = model.get("bodies", {})
    if body_name not in bodies:
        raise ChildrenError(f"no children model for body {body_name!r}")
    return bodies[body_name]


def generate_markdown_matrix(model: dict) -> str:
    """Human-readable Markdown matrix of the children model."""
    lines = ["# Children / body-membership model — per container (issue #6)", ""]
    lines.append("| Body | Child (wrapper) | Relationship | Chain | Kinds | Card | Prefixes | Source |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for body_name in sorted(model["bodies"]):
        for e in model["bodies"][body_name]:
            lines.append(
                f"| {body_name} | {e['wrapper']} | {e['relationship']} | "
                f"{' → '.join(e['chain'])} | {', '.join(e['kinds'])} | "
                f"{e['cardinality'] or ''} | {', '.join(e['prefixes'])} | "
                f"{e['source_rule']}:{e['source_line']} |"
            )
    return "\n".join(lines) + "\n"


def write_children(model: dict, out_json: Path, out_md: Path | None = None,
                   out_parity: Path | None = None) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    if out_md is not None:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(generate_markdown_matrix(model), encoding="utf-8")
    if out_parity is not None:
        out_parity.parent.mkdir(parents=True, exist_ok=True)
        out_parity.write_text(model.get("parity_report", ""), encoding="utf-8")
