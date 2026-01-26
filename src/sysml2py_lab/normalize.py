from __future__ import annotations

import re


_ws = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """
    Normalize SysML text for MVP equivalence.

    Rules (MVP):
      - Trim each line
      - Drop fully-empty lines
      - Collapse internal whitespace runs to a single space
      - Keep braces and semicolons as-is (we’re not tokenizing deeply yet)
    """
    out_lines: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        s = _ws.sub(" ", s)
        out_lines.append(s)
    return "\n".join(out_lines) + ("\n" if out_lines else "")
