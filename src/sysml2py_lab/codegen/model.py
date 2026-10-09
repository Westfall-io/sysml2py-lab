"""Metadata model for deterministic AST-class codegen (issue #9).

Loads the committed spec artifacts and exposes frozen descriptors that the
Jinja templates render into the generated ``sysml2py`` package:

- ``Rule`` — one language_spec.json rule
- ``NodeKind`` — a rule that acts as a generated node kind (class)
- ``BodySlot`` — one children.json membership slot
- ``DispatchMap`` — ``{parent_body: {member_kind: child_kind}}``

Everything here is derived from committed metadata; nothing is hand-tuned at
generation time except (a) the explicit ``EXTRA_KINDS`` set below (composites
the 266-class hand-written API carried but that are not grammar rules — e.g.
``RootNamespace``/``DefinitionElement`` dispatch roots), and (b) the
``IR_KIND_ALIASES`` table further down — a hand-maintained mapping from the
coarse issue-#7/#8 IR kinds (``package``, ``part``, ...) to generated class
names.  ``IR_KIND_ALIASES`` is the most load-bearing artifact in the cast
(both the registry-lift and the pinned coverage gate run through it).

The generator is deterministic: loading the same spec files always yields the
same descriptors, so ``generate`` is byte-reproducible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..grammar.spec import load_spec

# Extra class names generated alongside plain grammar rules.  RootNamespace /
# DefinitionElement are also real top-level grammar rules, so they are already
# generated and their EXTRA_KINDS entry is a belt-and-braces no-op (skipped by
# `seen`).  The genuine additions are the eight hand-invented helper composites
# the 0.5.3 classes.py carried (expression/operand wrappers + CommentSysML) —
# pinned unconditionally by test `test_extra_kind_composites_present`.
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
    owned_body: str | None = None  # XxxBody this rule contains (dispatch ctx)


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
        self._node_kinds_cache: list[NodeKind] | None = None
        self._owned_body_cache: dict[str, str | None] = {}

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
        if self._node_kinds_cache is not None:
            return self._node_kinds_cache
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
        # Attach owned-body (dispatch context) to every node kind: build fresh
        # NodeKind instances carrying their owned-body, since the dataclass is
        # immutable.
        kinds = [
            NodeKind(
                name=k.name,
                rule=k.rule,
                is_composite=k.is_composite,
                owned_body=self.owned_body_for(k.name),
            )
            for k in kinds
        ]
        self._node_kinds_cache = kinds
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

    # -- owned-body (dispatch context) ------------------------------------

    @staticmethod
    def _collect_calls(body: dict, out: list[str]) -> None:
        """Collect call-target names referenced in a rule body (recursive)."""
        if not isinstance(body, dict):
            return
        if body.get("kind") == "call":
            out.append(body.get("name", ""))
        for v in body.values():
            if isinstance(v, dict):
                CodegenModel._collect_calls(v, out)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, dict):
                        CodegenModel._collect_calls(x, out)

    def owned_body_for(self, cls_name: str) -> str | None:
        """The body rule this class OWNS (contained ``XxxBody``), or None.

        Follows the rule's call references (and through fragment rules) to the
        ``XxxBody`` the construct is defined to contain, then follows a body
        rule whose own body is a single delegation to another ``*Body`` (so
        ``UsageBody`` resolves to the real ``DefinitionBody`` it contains).
        ``PartUsage`` owns ``UsageBody``; ``Package`` owns ``PackageBody``.
        Memoized; depth-guarded against cycles.
        """
        if cls_name in self._owned_body_cache:
            return self._owned_body_cache[cls_name]
        res = self._owned_body_for(cls_name, 0)
        self._owned_body_cache[cls_name] = res
        return res

    def _owned_body_for(self, cls_name: str, _depth: int) -> str | None:
        if _depth > 25:
            return None
        # memoized result (the live cycle guard is the _depth bound above;
        # the cache is only ever populated by the public wrapper post-resolve)
        if cls_name in self._owned_body_cache:
            return self._owned_body_cache[cls_name]
        r = self._by_name.get(cls_name)
        if r is None:
            return None
        calls: list[str] = []
        self._collect_calls(r.get("body", {}), calls)
        for c in calls:
            if c.endswith("Body") and c in self._by_name:
                # a body rule that merely delegates to another body rule is an
                # alias — follow it so we land on the real containing body.
                rc = self._by_name.get(c)
                sub: list[str] = []
                if rc is not None:
                    self._collect_calls(rc.get("body", {}), sub)
                if len(sub) == 1 and sub[0].endswith("Body") and sub[0] in self._by_name:
                    sub_res = self._owned_body_for(sub[0], _depth + 1)
                    if sub_res is not None:
                        return sub_res
                return c
            if c in self._by_name:
                res = self._owned_body_for(c, _depth + 1)
                if res is not None:
                    return res
        return None

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

        The child kind for a member is the member's own name when it is a
        modelled node kind, else the slot's ``wrapper`` (or the member kind
        itself when there is no wrapper, which may then be Unsupported at
        dispatch time).  The ``chain`` field is NOT consulted here.
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

    def owned_body_map(self) -> dict[str, str]:
        """``{class_name: owned_body}`` for every node kind (dispatch context)."""
        return {k.name: k.owned_body for k in self.node_kinds() if k.owned_body}

    # -- provenance -------------------------------------------------------

    def provenance(self, repo_root: Path, generator_version: str, generated_at: str | None = None) -> dict:
        def _h(p: Path) -> str:
            return hashlib.sha256(p.read_bytes()).hexdigest()

        spec_hash = hashlib.sha256()
        for p in (
            repo_root / "spec" / "relationships" / "children.json",
            repo_root / "spec" / "relationships" / "modifiers.json",
            repo_root / "spec" / "overlay" / "assignments.json",
        ):
            spec_hash.update(p.read_bytes())
        # Never render None (Jinja tojson would emit `null` — invalid Python).
        stamp = generated_at or "1970-01-01T00:00:00Z"
        return {
            "grammar_sha256": _h(repo_root / "spec" / "language_spec.json"),
            "spec_sha256": spec_hash.hexdigest(),
            "children_sha256": _h(repo_root / "spec" / "relationships" / "children.json"),
            "modifiers_sha256": _h(repo_root / "spec" / "relationships" / "modifiers.json"),
            "corpus_manifest_sha256": _h(repo_root / "corpus" / "manifest.json"),
            "generator_version": generator_version,
            "generated_at": stamp,
        }
