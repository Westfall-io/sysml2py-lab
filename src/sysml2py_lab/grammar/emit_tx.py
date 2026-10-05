"""Regenerate textX `.tx` grammar text from the inlined language spec.

This is the `.tx` regenerator for issue #4.  It consumes the inlined spec
(fragments already expanded), applies a curated assignment overlay (the
hand-added assignment slot names that were historically bolted into the
committed `.tx`), and emits textX grammar text in the same shape as the
committed `sysml2py/src/sysml2py/grammar/*.tx` files.

The committed `.tx` is NOT byte-reproducible from the `.xtext` because the
assignment names (`prefix=`, `usage=`, `usageExtension+=`, ...) were hand
added.  This module makes that curation an explicit, versioned overlay
(`spec/overlay/assignments.json`) instead of hidden regex special-cases.

Fidelity:
- Literals are quoted at emit time (textX requires quotes).  The spec's lexer
  stores literal body values unquoted (`abstract` -> `'abstract'`).
- A `card` on ANY node kind is rendered (`(a | b)?`, `c*`, `'x'+`), never
  silently dropped.
- Xtext actions (`{Type.feature = current}`) are parked: rendered as a `#`
  comment so the file stays textX-loadable and the construct is not silently
  deleted.  See issue #4 for the action-handling follow-up.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .spec import SPEC_SCHEMA

#: Node kinds whose card must be rendered.
_CARDED = {"seq", "group", "alt", "lit", "assign", "xref", "pred", "call"}


def _load_overlay() -> dict:
    default: dict = {}
    json_path = Path(__file__).resolve().parents[3] / "spec" / "overlay" / "assignments.json"
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8"))
        default.update(data.get("entries", {}))
    return default


def _quote_literal(value: str) -> str:
    """Quote a literal value for textX emit."""
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value
    return f"'{value}'"


def _el_to_tx(el: dict, indent: int = 0) -> str:
    """Render one element tree node to .tx text."""
    if not isinstance(el, dict):
        return ""
    kind = el.get("kind")
    card = el.get("card", "") or ""
    if kind == "seq":
        inner = " ".join(_el_to_tx(i, indent) for i in el.get("items", []))
        if card:
            return f"({inner}){card}"
        return inner
    if kind == "alt":
        inner = " | ".join(_el_to_tx(c, indent) for c in el.get("choices", []))
        if card:
            return f"({inner}){card}"
        return inner
    if kind == "group":
        inner = _el_to_tx(el.get("body", {}), indent)
        return "(" + inner + ")" + card
    if kind == "lit":
        return _quote_literal(el.get("value", "")) + card
    if kind == "call":
        name = el.get("name", "")
        return name + card
    if kind == "xref":
        type_ = el.get("type", "")
        ref = el.get("ref")
        if ref and ref.get("name"):
            return f"[{type_}|{ref['name']}]" + card
        return f"[{type_}]" + card
    if kind == "action":
        # park Xtext actions as a comment so the .tx stays loadable
        act = " ".join(str(el.get(k, "")) for k in ("type", "assign", "feature"))
        return f"# action: {act}".strip() if act.strip() else "# action"
    if kind == "assign":
        name = el.get("name", "")
        op = el.get("op", "=")
        val = _el_to_tx(el.get("value", {}), indent)
        return f"{name}{op}{val}" + card
    if kind == "pred":
        arrow = el.get("arrow", "=>")
        body = _el_to_tx(el.get("body", {}), indent)
        return f"{arrow} {body}" + card
    if kind == "tok":
        return el.get("value", "")
    if kind in ("terminal_body", "enum_body"):
        return " ".join(t.get("value", "") for t in el.get("tokens", []))
    return ""


def _global_slot_map(overlay: dict) -> dict[str, str]:
    """Build callee_fragment -> slot_name from ALL dict overlay entries.

    e.g. {'OccurrenceUsagePrefix': 'prefix', 'Usage': 'usage', ...} so any
    rule body referencing those fragments can render the assignment slot,
    matching the committed .tx (which keeps fragment calls with slots).
    """
    slot_map: dict[str, str] = {}
    for entry in overlay.values():
        if not isinstance(entry, dict):
            continue
        for slot, callee in entry.items():
            if isinstance(callee, str) and isinstance(slot, str):
                slot_map.setdefault(callee, slot)
    return slot_map


def _apply_overlay_to_rule(rule: dict, overlay_entry, global_slots: dict[str, str] | None = None) -> str:
    """Render a rule applying a dict overlay entry (assignment slot names).

    A dict entry like `{"prefix": "RefPrefix"}` maps a callee fragment name to
    the assignment slot name `classes.py` keys off.  For a rule body that is a
    sequence of fragment calls (e.g. PartUsage: OccurrenceUsagePrefix
    PartUsageKeyword Usage), this rewrites the matching call node into an
    assignment `prefix=OccurrenceUsagePrefix`, `usage=Usage`, etc., then
    renders the body.

    When `global_slots` is given, ANY call to a known prefix/usage fragment in
    the rule's body is assigned its slot (this is how OccurrenceUsage /
    PartUsage get `prefix=...` even though they are not overlay keys).

    Called ONLY on pre-inline rule bodies (the calls must still be present).
    """
    body = rule.get("body", {})
    if overlay_entry is not None and not isinstance(overlay_entry, dict):
        # string entry: whole-rule body replacement (the 'broken' rules)
        return overlay_entry

    # callee_name -> slot_name  (e.g. "Usage" -> "usage")
    slot_by_callee = {}
    if isinstance(overlay_entry, dict):
        slot_by_callee = {callee: slot for slot, callee in overlay_entry.items()
                          if isinstance(callee, str) and isinstance(slot, str)}
    if global_slots:
        for callee, slot in global_slots.items():
            slot_by_callee.setdefault(callee, slot)

    if not slot_by_callee:
        return _el_to_tx(body)

    def rewire(el):
        if not isinstance(el, dict):
            return
        k = el.get("kind")
        if k == "call" and el.get("name") in slot_by_callee:
            callee = el["name"]
            slot = slot_by_callee[callee]
            # assignment form: usage=Usage  (plus any overlay modifier)
            el["kind"] = "assign"
            el["name"] = slot
            el["op"] = "="
            el["value"] = {"kind": "call", "name": callee, "card": el.get("card")}
            el.pop("card", None)
        for chk, ch in [
            ("items", el.get("items", [])), ("choices", el.get("choices", [])),
            ("body", [el["body"]] if el.get("body") is not None else []),
        ]:
            for c in ch:
                rewire(c)

    body = dict(body)
    rewire(body)
    return _el_to_tx(body)


def _rule_to_tx(rule: dict, overlay_entry=None, global_slots: dict[str, str] | None = None) -> str:
    name = rule.get("name", "")
    kind_ = rule.get("rule_kind", "rule")
    if kind_ in ("terminal", "enum"):
        return f"{name}:\n\t{_el_to_tx(rule.get('body', {}))}\n;"
    if overlay_entry is not None:
        body = _apply_overlay_to_rule(rule, overlay_entry, global_slots)
    elif global_slots:
        body = _apply_overlay_to_rule(rule, None, global_slots)
    else:
        body = _el_to_tx(rule.get("body", {}))
    return f"{name}:\n\t{body}\n;"


def emit_tx_str(spec: dict, file: str | None = None, original_spec: dict | None = None) -> str:
    """Emit .tx text for the inlined spec.

    If `file` is given (e.g. "SysML.xtext"), only rules whose source file is
    that grammar are emitted — used for per-file .tx parity comparison.
    Otherwise all rules are emitted in file order with import headers.

    `original_spec` (optional) carries the pre-inline rule table; overlay-keyed
    rules are rendered from it so the assignment slots (prefix=, usage=, ...)
    have their fragment calls still present.
    """
    overlay = _load_overlay()
    global_slots = _global_slot_map(overlay)
    # prefer original rule bodies for overlay-keyed rules
    orig_by_name = {}
    if original_spec is not None:
        orig_by_name = {r["name"]: r for r in original_spec.get("rules", [])}
    parts: list[str] = []
    for f in spec.get("files", []):
        fname = f["file"]
        if file is not None and fname != file:
            continue
        if not file:
            if fname == "KerMLExpressions.xtext":
                parts.append("")
            elif fname == "KerML.xtext":
                parts.append("import KerMLExpressions\n")
            elif fname == "SysML.xtext":
                parts.append("import KerML\nimport KerMLExpressions\n")
        for rule in spec.get("rules", []):
            if rule.get("source", {}).get("file") != fname:
                continue
            rname = rule["name"]
            overlay_entry = overlay.get(rname)
            if overlay_entry is not None or global_slots:
                src_rule = orig_by_name.get(rname, rule)
                parts.append(_rule_to_tx(src_rule, overlay_entry, global_slots))
            else:
                parts.append(_rule_to_tx(rule, None))
    return "\n".join(parts) + "\n"


def emit_tx(spec: dict, out_dir: Path, original_spec: dict | None = None) -> str:
    """Write SysML.tx / KerML.tx / KerMLExpressions.tx into out_dir.

    Writes per-grammar files using each rule's source.file.  Returns the
    combined text (for tests).  `original_spec` (optional) is the pre-inline
    spec used for overlay-keyed rules.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    src_files = [f["file"] for f in spec.get("files", [])]
    base = out_dir
    combined_parts = []
    for src in sorted(src_files):
        tx_name = src.replace(".xtext", ".tx")
        t = emit_tx_str(spec, file=src, original_spec=original_spec)
        (base / tx_name).write_text(t, encoding="utf-8")
        combined_parts.append(f"### {tx_name}\n{t}")
    combined = "\n".join(combined_parts)
    return combined
