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
                "StructureUsageElement"],   # wrapper -> element chain
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

from .inline import _import_order


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

    A capability rule (name ends with 'Element', e.g. `StructureUsageElement`,
    `OccurrenceUsageElement`, `DefinitionElement`) is an alt of calls; expand
    only those.  A call whose target is NOT a capability is itself a concrete
    kind (e.g. `PartUsage`, `Package`), so return its name and stop.
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

    is_capability = call_name.endswith("Element")
    if not is_capability:
        # a concrete kind (or a plain rule) — itself is the answer
        return [call_name]

    # capability: alt of calls
    if k != "alt":
        # not an alt after all — treat as opaque concrete kind
        return [call_name]

    out: list[str] = []
    for ch in body.get("choices", []):
        chk = ch.get("kind")
        if chk == "call":
            out.extend(_element_kinds(ch["name"], by_name, seen | {call_name}, depth + 1))
        elif chk == "seq":
            for inner in ch.get("items", []):
                if inner.get("kind") == "call":
                    out.extend(_element_kinds(inner["name"], by_name, seen | {call_name}, depth + 1))
                    break
    return sorted(set(out))


def _expand_bodyitem(bodyitem_el: dict, by_name: dict[str, dict]) -> list[dict]:
    """Expand a bare `*BodyItem`/`*BodyPart` call into the member
    alternatives it permits, as separate child entries.

    A `*BodyPart` (e.g. `StateBodyPart`) is a thin wrapper: `=> BodyItem` or
    a seq of `=> BodyItem` + extra members — chase it to the underlying
    `*BodyItem`.  Handles direct member assigns, seq choices (an optional
    succession prefix + a member), and pred-guarded choices (=> Import).
    """
    bname = bodyitem_el.get("name", "")
    cur = bname
    # chase *BodyPart -> *BodyItem
    while cur.endswith("BodyPart"):
        pr = by_name.get(cur)
        if pr is None:
            return []
        pbody = pr.get("body")
        nxt = None
        if pbody is not None:
            for el in _walk(pbody):
                if el.get("kind") == "call" and el["name"].endswith("BodyItem"):
                    nxt = el["name"]
                    break
        if not nxt:
            break
        cur = nxt
    brule = by_name.get(cur)
    if brule is None:
        return []
    body = brule.get("body")
    if body is None or body.get("kind") != "alt":
        return []
    out = []
    for ch in body.get("choices", []):
        chk = ch.get("kind")
        if chk == "assign" and ch.get("name", "").startswith("owned"):
            # direct member (may also carry succession prefix info)
            sub = _item_entry(ch, by_name, brule)
            out.append(sub)
        elif chk == "call" and ch.get("name", "").endswith("BodyItem"):
            # delegation to a base body (e.g.
            # RequirementBodyItem: DefinitionBodyItem | SubjectMember | ...)
            # — inherit the base's members
            out.extend(_expand_bodyitem(ch, by_name))
        elif chk == "seq":
            # seq[group(assign(EmptySuccessionMember)?), assign(owned+=Member)]
            # — pick the member assignment that is not a succession prefix
            member = None
            prefixes = []
            for inner in ch.get("items", []):
                for a in _walk(inner):
                    if a.get("kind") == "assign" and a.get("name", "").startswith("owned"):
                        v = a.get("value")
                        if v is not None and v.get("kind") == "call":
                            if v["name"].endswith("Member") and v["name"] != "EmptySuccessionMember":
                                member = a
                            elif v["name"] == "EmptySuccessionMember":
                                prefixes.append("EmptySuccessionMember")
            if member is not None:
                sub = _item_entry(member, by_name, brule)
                sub["prefixes"] = sorted(set(sub.get("prefixes", [])) | set(prefixes))
                out.append(sub)
        elif chk == "pred":
            # pred => assign(owned+=call(Import))
            pb = ch.get("body")
            if pb is not None:
                for a in _walk(pb):
                    if a.get("kind") == "assign" and a.get("name", "").startswith("owned"):
                        v = a.get("value")
                        if v is not None and v.get("kind") == "call":
                            sub = _item_entry(a, by_name, brule)
                            out.append(sub)
    return out


def _item_entry(item_el: dict, by_name: dict[str, dict], owner_rule: dict | None = None) -> dict:
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
        # wrapper members: seq[MemberPrefix, assign(ownedRelatedElement+=call(Element))]
        if k == "seq":
            found = False
            for inner in _walk(body):
                if inner.get("kind") == "assign" and inner.get("name", "").startswith("owned"):
                    v = inner.get("value")
                    if v is not None and v.get("kind") == "call":
                        nxt = v["name"]
                        if nxt not in seen:
                            chain.append(nxt)
                            cur = nxt
                            found = True
                        break
            if not found:
                break
            continue
        if k == "alt":
            # a top-level capability alt: the element rule that decides kinds
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
        break

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

    containers = [r for r in rules if _is_container(r)]
    bodies: dict[str, list[dict]] = {}
    for cont in sorted(containers, key=lambda r: r["name"]):
        body = cont.get("body")
        if body is None:
            continue
        entries: list[dict] = []
        # container body is usually alt{ ';' | '{' BodyItem* '}' } or
        # seq[lit('{'), BodyItem*, lit('}')]; gather every assign-of-member
        # call and its card from the tree, plus pred-guarded imports.
        candidate_els = []
        for el in _walk(body):
            # assigned member (ownedRelationship+=call(Member))
            if el.get("kind") == "assign" and el.get("name", "").startswith("owned"):
                candidate_els.append(el)
            # bare body-item call (DefinitionBodyItem[*], StateBodyPart) —
            # expand below
            elif el.get("kind") == "call" and (
                el.get("name", "").endswith("BodyItem")
                or el.get("name", "").endswith("BodyPart")
            ):
                candidate_els.append(el)
        seen_wrappers = set()
        for el in candidate_els:
            if el.get("kind") == "call" and (
                el.get("name", "").endswith("BodyItem")
                or el.get("name", "").endswith("BodyPart")
            ):
                for sub in _expand_bodyitem(el, by_name):
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
            entry = _item_entry(el, by_name, by_name.get(wrapper, {}))
            # the entry's item rule is the *call target* (the member), not
            # this container's rule; fix source provenance to the member rule
            mrule = by_name.get(wrapper) or {"name": wrapper, "source": {}}
            entry["source_rule"] = wrapper
            entry["source_line"] = mrule.get("source", {}).get("line")
            key = (wrapper, entry["cardinality"])
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
