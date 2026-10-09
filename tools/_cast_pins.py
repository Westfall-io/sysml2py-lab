"""Pinned cast expectations (issue #9, r4/r7).

Side-effect-free: importing this module must not touch sys.path, sys.argv,
or import sysml2py.  Kept separate from tools/cast_ast_classes.py so the
acceptance test can import it without triggering that tool's CLI-side
path setup (r7 W1).
"""

from __future__ import annotations

# Pinned expected set of aliased classes (C1): derived as a literal so that
# emptying IR_KIND_ALIASES cannot vacate the cast gates.
REQUIRED_ALIASED_CLASSES = frozenset(
    {
        "Package",
        "PartUsage",
        "AttributeUsage",
        "ActionUsage",
        "ItemUsage",
        "PortUsage",
        "StateUsage",
        "TransitionUsage",
        "ConstraintUsage",
        "InterfaceUsage",
        "ConnectionUsage",
        "OccurrenceUsage",
        "RequirementUsage",
        "UseCaseUsage",
        "ObjectiveRequirementUsage",
        "SubjectUsage",
        "ActorUsage",
        "Message",
        "Succession",
        "AliasMember",
        "Import",
        "Comment",
        "RootNamespace",
    }
)
