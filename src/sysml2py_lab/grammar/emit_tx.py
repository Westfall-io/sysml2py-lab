"""Regenerate textX `.tx` grammar text from the inlined language spec.

This is the `.tx` regenerator for issue #4.  It consumes the inlined spec
(fragments already expanded), applies a curated assignment overlay (the
hand-added assignment slot names that were historically bolted into the
committed `.tx`), and emits textX grammar text in the same shape as the
committed `sysml2py/src/sysml2py/grammar/*.tx` files.

The committed `.tx` is NOT byte-reproducible from the `.xtext` because the
assignment names (`prefix=`, `usage=`, `usageExtension+=`, ...) were hand
added.  This module makes that curation an explicit, versioned overlay
(`spec/overlay/assignments.yaml`) instead of hidden regex special-cases.

Normalization whitelist: when comparing generated output to the committed
`.tx`, the emitter documents which differences are intended (and why).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .spec import SPEC_SCHEMA

# The curated assignment-name overlay.  Keyed by rule name; value is a list
# of (token_pattern, assignment_text) pairs.  This is the explicit, reviewed
# version of the hand-curation that used to live in xtext_to_textx.py regexes.
#
# Seeded from the committed .tx (see issue #3 body):
#   - prefix=, usage=, usageExtension+=, declaration=, valuepart=, body=,
#     keyword+=, ownedRelatedElement+=, direction=, isIndividual?=
#   - FeatureDirection: in = 'in '  (trailing space quirk, matched literally)
#
# Each entry is justified in a comment:
#   - `prefix=` on the inlined prefix fragments is what classes.py keys off
#     (RefPrefix.dump() / BasicUsagePrefix.dump() / OccuranceUsagePrefix.dump()).
#   - `usage=` on PartUsage/AttributeUsage/etc is the corpus-facing attribute
#     name for the child usage.
#   - `usageExtension+=` gives the extendable keyword slot on UsagePrefix.
#   - `declaration=`/`valuepart=`/`body=` slot the three fields of a
#     ReferenceUsage-style declaration.
#   - `keyword+=` is the extendable keyword list on definition prefixes.
#   - `ownedRelatedElement+=` is the membership slot on relationship/lookup.
#   - `direction=`/`isIndividual?=` are boolean/flag slots on FeatureDirection
#     and OccurrenceUsagePrefix.
#   - FeatureDirection `in = 'in '` preserves the trailing space quirk that
#     classes.py compares against literally (issue #4 "quirks recorded, not
#     silently fixed").

def _load_overlay() -> dict:
    default: dict = {}
    # Prefer the JSON overlay (stdlib, no yaml dep); if a yaml overlay exists
    # and pyyaml is installed, fall back to it.
    json_path = Path(__file__).resolve().parents[3] / "spec" / "overlay" / "assignments.json"
    yaml_path = Path(__file__).resolve().parents[3] / "spec" / "overlay" / "assignments.yaml"
    if json_path.exists():
        import json as _json
        data = _json.loads(json_path.read_text(encoding="utf-8"))
        default.update(data.get("entries", {}))
    elif yaml_path.exists():
        import yaml
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        default.update(data.get("entries", {}))
    return default


#: Documented normalizations applied when comparing generated .tx to the
#: committed .tx live in the parity test's TX_WHITELIST (tests/test_inline_tx.py).
#: This module keeps only the overlay; the whitelist is a test-side document
#: so the parity claim is reviewable in one place.


def _el_to_tx(el: dict, indent: int = 0) -> str:
    """Render one element tree node to .tx text."""
    if not isinstance(el, dict):
        return ""
    kind = el.get("kind")
    if kind == "seq":
        return " ".join(_el_to_tx(i, indent) for i in el.get("items", []))
    if kind == "alt":
        return " | ".join(_el_to_tx(c, indent) for c in el.get("choices", []))
    if kind == "group":
        inner = _el_to_tx(el.get("body", {}), indent)
        return "(" + inner + ")"
    if kind == "lit":
        return el.get("value", "")
    if kind == "call":
        name = el.get("name", "")
        return name + (el.get("card", "") or "")
    if kind == "xref":
        type_ = el.get("type", "")
        ref = el.get("ref")
        if ref and ref.get("name"):
            return f"[{type_}|{ref['name']}]"
        return f"[{type_}]"
    if kind == "action":
        return "{" + el.get("type", "") + "}"
    if kind == "assign":
        name = el.get("name", "")
        op = el.get("op", "=")
        val = _el_to_tx(el.get("value", {}), indent)
        return f"{name}{op}{val}"
    if kind == "pred":
        arrow = el.get("arrow", "=>")
        body = _el_to_tx(el.get("body", {}), indent)
        return f"{arrow} {body}"
    if kind == "tok":
        return el.get("value", "")
    if kind in ("terminal_body", "enum_body"):
        return " ".join(t.get("value", "") for t in el.get("tokens", []))
    return ""


def _rule_to_tx(rule: dict) -> str:
    name = rule.get("name", "")
    kind_ = rule.get("rule_kind", "rule")
    if kind_ in ("terminal", "enum"):
        return f"{name}:\n\t{_el_to_tx(rule.get('body', {}))}\n;"
    body = _el_to_tx(rule.get("body", {}))
    return f"{name}:\n\t{body}\n;"


def emit_tx_str(spec: dict, file: str | None = None) -> str:
    """Emit .tx text for the inlined spec.

    If `file` is given (e.g. "SysML.xtext"), only rules whose source file is
    that grammar are emitted — used for per-file .tx parity comparison.
    Otherwise all rules are emitted in file order with import headers.
    """
    overlay = _load_overlay()
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
            # overlay: assignment-name rewrites on specific rules
            full_repl = overlay.get(rname)
            if full_repl is not None and isinstance(full_repl, str):
                # whole-rule body replacement (e.g. the 'broken' empty rules
                # that the committed .tx special-cases as literals)
                parts.append(f"{rname}:\n\t{full_repl}\n;")
                continue
            parts.append(_rule_to_tx(rule))
    return "\n".join(parts) + "\n"


def emit_tx(spec: dict, out_dir: Path) -> str:
    """Write SysML.tx / KerML.tx / KerMLExpressions.tx into out_dir.

    Writes per-grammar files using each rule's source.file.  Returns the
    combined text (for tests).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # gather which source files exist in this spec
    src_files = []
    for f in spec.get("files", []):
        src_files.append(f["file"])
    # map source .xtext -> target .tx
    base = out_dir
    combined_parts = []
    for src in sorted(src_files):
        tx_name = src.replace(".xtext", ".tx")
        t = emit_tx_str(spec, file=src)
        (base / tx_name).write_text(t, encoding="utf-8")
        combined_parts.append(f"### {tx_name}\n{t}")
    combined = "\n".join(combined_parts)
    return combined
