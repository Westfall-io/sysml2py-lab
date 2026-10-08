"""Metadata model for deterministic AST-class codegen (issue #9).

Loads the committed spec artifacts and exposes frozen descriptors that the
Jinja templates render into the generated ``sysml2py`` package:

- ``Rule`` — one language_spec.json rule
- ``NodeKind`` — a rule that acts as a generated node kind (class)
- ``BodySlot`` — one children.json membership slot
- ``DispatchMap`` — ``{parent_body: {member_kind: child_kind}}``

Everything here is derived from committed metadata; nothing is hand-tuned at
generation time except the explicit ``EXTRA_KINDS`` set below (composites the
266-class hand-written API carried but that are not grammar rules — e.g.
``RootNamespace``/``DefinitionElement`` dispatch roots).

The generator is deterministic: loading the same spec files always yields the
same descriptors, so ``generate`` is byte-reproducible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..grammar.spec import load_spec

# Composites the 266-class API carried that are not plain grammar rules but
# are required for round-trip compatibility (dispatch roots / wrappers).
EXTRA_KINDS: tuple[str, ...] = (
    "RootNamespace",
    "DefinitionElement",
    # hand-invented helper composites from the 0.5.3 classes.py (not grammar
    # rules, but part of the public class surface the epic preserves)
    "ActionBodyItemTarget",
    "AdditiveOperand",
    "AndOperand",
    "CommentSysML",
    "EqualityOperand",
    "MultiplicativeOperand",
    "MultiplicityRelatedElement",
    "RelationalOperand",
    "SequenceOperand",
    "Unsupported",  # excluded from generation (template provides by hand)
)

# IR-kind -> generated-class-name alias map (issue #9 dispatch).
# The issue-#7/#8 IR is a coarse brace-block parse: its `kind` values are
# tokens like `package`, `part`, `attribute`; the generated classes use the
# fine-grained grammar class names.  The dispatch maps IR kinds onto the
# generated classes so an IR tree can be lifted into generated nodes.
IR_KIND_ALIASES: dict[str, str] = {
    "action": "ActionUsage",
    "actor": "ActorUsage",
    "alias": "AliasMember",
    "attribute": "AttributeUsage",
    "brace_close": "Unsupported",
    "brace_open": "Unsupported",
    "comment": "Comment",
    "connection": "ConnectionUsage",
    "constraint": "ConstraintUsage",
    "import": "Import",
    "interface": "InterfaceUsage",
    "item": "ItemUsage",
    "message": "Message",
    "objective": "ObjectiveRequirementUsage",
    "occurrence": "OccurrenceUsage",
    "package": "Package",
    "part": "PartUsage",
    "port": "PortUsage",
    "requirement": "RequirementUsage",
    "root": "RootNamespace",
    "state": "StateUsage",
    "subject": "SubjectUsage",
    "succession": "Succession",
    "transition": "TransitionUsage",
    "unknown": "Unsupported",
    "use_case": "UseCaseUsage",
}


@dataclass(frozen=True)
class Rule:
    """One rule from language_spec.json."""

    name: str
    rule_kind: str  # rule | fragment | enum | terminal
    returns: str | None
    body: dict | None
    source_file: str | None
    source_line: int | None


@dataclass(frozen=True)
class NodeKind:
    """A generated node class: a rule (or composite) that has a class."""

    name: str
    rule: Rule | None
    is_composite: bool = False


@dataclass(frozen=True)
class BodySlot:
    """One membership slot from children.json."""

    body: str
    wrapper: str
    relationship: str
    cardinality: str | None
    kinds: tuple[str, ...]
    chain: tuple[str, ...]


@dataclass(frozen=True)
class ModifierSlot:
    """One modifier slot from modifiers.json."""

    name: str
    tokens: tuple[str, ...]
    kind: str  # flag | value | enum
    cardinality: str | None
    mutually_exclusive_with: tuple[str, ...] = ()


class CodegenModel:
    """Loaded, frozen metadata for AST-class generation."""

    def __init__(self, spec: dict, children: dict | None = None, modifiers: dict | None = None):
        self._spec = spec
        # children.json shape: {"bodies": {name: [slots]}, "parity_report": str}
        self._children_bodies = (children or {}).get("bodies", {})
        self._modifiers = modifiers or {}
        self._by_name: dict[str, dict] = {}
        for r in spec.get("rules", []):
            self._by_name[r["name"]] = r

    # -- construction -----------------------------------------------------

    @classmethod
    def load(cls, repo_root: Path) -> CodegenModel:
        spec = load_spec(repo_root / "spec" / "language_spec.json")
        children = json.loads((repo_root / "spec" / "relationships" / "children.json").read_text())
        modifiers = json.loads((repo_root / "spec" / "relationships" / "modifiers.json").read_text())
        return cls(spec, children, modifiers)

    # -- lookups ----------------------------------------------------------

    def rule(self, name: str) -> Rule | None:
        r = self._by_name.get(name)
        if r is None:
            return None
        return Rule(
            name=r["name"],
            rule_kind=r.get("rule_kind", ""),
            returns=r.get("returns"),
            body=r.get("body"),
            source_file=(r.get("source") or {}).get("file"),
            source_line=(r.get("source") or {}).get("line"),
        )

    def rules(self) -> list[Rule]:
        return [self.rule(r["name"]) for r in self._spec.get("rules", [])]  # type: ignore[misc]

    def node_kinds(self) -> list[NodeKind]:
        """All grammar rules that are generated as node classes.

        Includes plain rules, fragments, enums, and terminals — the 266-class
        hand-written API generated classes for fragments/enums too (e.g.
        ``PackageBody``, ``FeatureDirection``), so parity requires generating
        them all.  ``Unsupported`` is excluded because the template defines it
        by hand (it is a loss-minimizing fallback, not a grammar node).
        """
        kinds: list[NodeKind] = []
        seen: set[str] = set()
        for r in self._spec.get("rules", []):
            name = r["name"]
            if name in seen or name in ("Unsupported",):
                continue
            seen.add(name)
            kinds.append(NodeKind(name=name, rule=self.rule(name)))
        for name in EXTRA_KINDS:
            if name in ("Unsupported",) or name in seen:
                continue
            seen.add(name)
            kinds.append(NodeKind(name=name, rule=self.rule(name), is_composite=True))
        return kinds

    def body_slots(self, body: str) -> list[BodySlot]:
        out = []
        for slot in self._children_bodies.get(body, []):
            out.append(
                BodySlot(
                    body=body,
                    wrapper=slot.get("wrapper", ""),
                    relationship=slot.get("relationship", "ownedRelationship"),
                    cardinality=slot.get("cardinality"),
                    kinds=tuple(slot.get("kinds", [])),
                    chain=tuple(slot.get("chain", [])),
                )
            )
        return out

    def body_names(self) -> list[str]:
        return sorted(self._children_bodies.keys())

    def modifier_slots(self, prefix: str) -> list[ModifierSlot]:
        out = []
        for slot in self._modifiers.get(prefix, {}).get("slots", []):
            out.append(
                ModifierSlot(
                    name=slot.get("name", ""),
                    tokens=tuple(slot.get("tokens", [])),
                    kind=slot.get("kind", ""),
                    cardinality=slot.get("cardinality"),
                    mutually_exclusive_with=tuple(slot.get("mutually_exclusive_with", [])),
                )
            )
        return out

    def prefix_names(self) -> list[str]:
        return sorted(self._modifiers.keys())

    # -- dispatch ---------------------------------------------------------

    def dispatch_map(self) -> dict[str, dict[str, str]]:
        """``{body_name: {member_kind: child_kind}}`` derived from children.json.

        The child kind for a slot is the LAST element of the chain when the
        chain is a single-element path to the kind, else the wrapper.  We use
        the kind's own name when it is a modelled node kind; otherwise the
        wrapper becomes the dispatch target (and may be Unsupported).
        """
        out: dict[str, dict[str, str]] = {}
        known = {k.name for k in self.node_kinds()}
        for body in self.body_names():
            table: dict[str, str] = {}
            for slot in self.body_slots(body):
                for kind in slot.kinds:
                    child = kind if kind in known else (slot.wrapper or kind)
                    table[kind] = child
            out[body] = table
        return out

    # -- provenance -------------------------------------------------------

    def provenance(self, repo_root: Path, generator_version: str, generated_at: str | None = None) -> dict:
        h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        spec_hash = hashlib.sha256()
        for p in (
            repo_root / "spec" / "relationships" / "children.json",
            repo_root / "spec" / "relationships" / "modifiers.json",
            repo_root / "spec" / "overlay" / "assignments.json",
        ):
            spec_hash.update(p.read_bytes())
        return {
            "grammar_sha256": h(repo_root / "spec" / "language_spec.json"),
            "spec_sha256": spec_hash.hexdigest(),
            "children_sha256": h(repo_root / "spec" / "relationships" / "children.json"),
            "modifiers_sha256": h(repo_root / "spec" / "relationships" / "modifiers.json"),
            "corpus_manifest_sha256": h(repo_root / "corpus" / "manifest.json"),
            "generator_version": generator_version,
            "generated_at": generated_at,
        }
