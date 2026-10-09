from __future__ import annotations

from sysml2py_lab.model import dump_block_mvp
from sysml2py_lab.normalize import normalize_text
from sysml2py_lab.parse_blocks import parse_brace_blocks


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
