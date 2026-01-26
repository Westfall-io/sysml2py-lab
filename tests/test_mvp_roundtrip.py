from __future__ import annotations

from sysml2py_lab.normalize import normalize_text
from sysml2py_lab.parse_blocks import parse_brace_blocks
from sysml2py_lab.model import Block, Line


def dump_block_mvp(root: Block) -> str:
    """
    MVP dumper for the lab-side parse tree.
    (The generated sysml2py will have its own dumper.)
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


def test_mvp_roundtrip_normalized():
    sample = """
    part sensor {
       part camera {
          attribute mass;
       }
       item lens;
    }
    """

    root = parse_brace_blocks(sample)
    dumped = dump_block_mvp(root)

    assert normalize_text(dumped) == normalize_text(sample)
