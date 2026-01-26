from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Line:
    """A single source line (content only; newline not included)."""

    text: str


@dataclass
class Block:
    """
    A brace-delimited block.

    Example (conceptually):
      header_lines = ["part A"]
      children = [Line("attribute mass;"), Block(...)]
    """

    header_lines: list[Line] = field(default_factory=list)
    children: list[Line | "Block"] = field(default_factory=list)

    def walk_blocks(self) -> Iterable["Block"]:
        """Yield self and all descendant blocks (preorder)."""
        yield self
        for c in self.children:
            if isinstance(c, Block):
                yield from c.walk_blocks()
