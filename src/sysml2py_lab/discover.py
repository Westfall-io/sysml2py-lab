"""Corpus discovery (issue #8).

Extends the MVP flat prefix histogram with relationship-aware node-kind
counts from the IR classification layer (kinds come from the issue-6
children model, not a flat prefix guess).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .normalize import normalize_text
from .parse_blocks import parse_brace_blocks
from .model import Line
from .ir import parse_ir


@dataclass(frozen=True)
class DiscoveryResult:
    """Summary of corpus patterns for codegen."""

    files_scanned: int
    statement_prefix_counts: dict[str, int]
    node_kind_counts: dict[str, int] = field(default_factory=dict)


def iter_sysml_files(corpus_dir: Path) -> Iterable[Path]:
    """Yield .sysml files under corpus_dir."""
    yield from sorted(corpus_dir.rglob("*.sysml"))


def discover_corpus(corpus_dir: Path) -> DiscoveryResult:
    """
    Corpus discovery:

      - parse each file into brace blocks
      - count statement "prefix" = first token on each Line
      - also parse each file to IR and count node KINDS (relationship-aware:
        kinds come from the issue-6 children model via the IR, not from a
        flat prefix guess) — issue #8.
    """
    corpus_dir = corpus_dir.expanduser().resolve()
    counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    files = list(iter_sysml_files(corpus_dir))

    for p in files:
        text = p.read_text(encoding="utf-8")
        norm = normalize_text(text)
        root = parse_brace_blocks(norm)

        for b in root.walk_blocks():
            for child in b.children:
                if isinstance(child, Line):
                    tok = (child.text.strip().split(" ", 1)[0] if child.text.strip() else "")
                    if tok:
                        counts[tok] = counts.get(tok, 0) + 1

        # relationship-aware: node kinds from the IR classification layer
        ir_root = parse_ir(text)
        for n in ir_root.walk():
            if n.kind not in ("root", "brace_open", "brace_close", "block", "comment"):
                kind_counts[n.kind] = kind_counts.get(n.kind, 0) + 1

    return DiscoveryResult(
        files_scanned=len(files),
        statement_prefix_counts=counts,
        node_kind_counts=kind_counts,
    )
