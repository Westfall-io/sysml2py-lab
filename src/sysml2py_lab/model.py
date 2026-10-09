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


def dump_block_mvp(root: Block) -> str:
    """
    MVP dumper for the lab-side parse tree.
    (The generated sysml2py has its own dumper.)
    """
    out: list[str] = []

    def emit_block(b: Block, indent: int) -> None:
        ind = "  " * indent
        if b.header_lines:
            # For MVP, join header lines (rarely multiple)
            hdr = " ".join(h.text for h in b.header_lines).strip()
            out.append(f"{ind}{hdr} {{")
        else:
            out.append(f"{ind}{{")

        for c in b.children:
            if isinstance(c, Line):
                out.append(f"{ind}  {c.text}")
            else:
                emit_block(c, indent + 1)

        out.append(f"{ind}}}")

    # Root is an implicit container; dump children directly
    for c in root.children:
        if isinstance(c, Line):
            out.append(c.text)
        else:
            emit_block(c, 0)

    return "\n".join(out) + ("\n" if out else "")
