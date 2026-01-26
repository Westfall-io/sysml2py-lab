from __future__ import annotations

from .model import Block, Line


def parse_brace_blocks(text: str) -> Block:
    """
    Parse a SysML-ish file using a very simple brace-based heuristic.

    MVP assumptions:
      - Blocks open with "{" on a line (or after some header tokens on that line)
      - Blocks close with "}" on a line
      - Everything else is treated as a raw statement line

    This is intentionally *not* a full SysML parser. It's enough to:
      - preserve nesting
      - round-trip normalized structure/tokens
    """
    root = Block()
    stack: list[Block] = [root]

    def cur() -> Block:
        return stack[-1]

    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue

        # Handle lines that contain braces with optional header content.
        # Examples:
        #   part A {
        #   {
        #   } else? (we treat as just closing brace line if startswith })
        if "{" in s:
            before, _, after = s.partition("{")
            header = before.strip()
            if header:
                cur().children.append(Line(header))
            b = Block()
            # if there was a header line, treat it as block header instead
            if header:
                b.header_lines.append(Line(header))
                # remove the header line we added to children; we prefer it as header
                cur().children.pop()
            cur().children.append(b)
            stack.append(b)

            # Anything after "{" is treated as a statement inside the new block
            tail = after.strip()
            if tail:
                cur().children.append(Line(tail))
            continue

        if "}" in s:
            # Close one level per '}' occurrence (common is one)
            # If there is extra content, keep it as a line at the parent level.
            parts = s.split("}")
            closes = len(parts) - 1
            tail = parts[-1].strip()

            for _ in range(closes):
                if len(stack) > 1:
                    stack.pop()

            if tail:
                cur().children.append(Line(tail))
            continue

        # Plain statement line
        cur().children.append(Line(s))

    # If braces were unbalanced, we just return what we built.
    return root
