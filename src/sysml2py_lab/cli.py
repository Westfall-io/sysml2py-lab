from __future__ import annotations

import argparse
from pathlib import Path

from .discover import discover_corpus
from .codegen.emit import emit_sysml2py, EmitOptions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sysml2py-lab", description="Generate sysml2py from a SysML v2 corpus (MVP).")

    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="Scan corpus and print discovered statement keyword counts.")
    d.add_argument("corpus", type=Path)

    g = sub.add_parser("generate", help="Discover + emit a generated sysml2py package.")
    g.add_argument("corpus", type=Path)
    g.add_argument("--out", type=Path, default=Path("out"))
    g.add_argument("--version", default="0.0.0")

    args = p.parse_args(argv)

    if args.cmd == "discover":
        res = discover_corpus(args.corpus)
        print(f"files_scanned={res.files_scanned}")
        for k, v in sorted(res.statement_prefix_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{k}: {v}")
        return 0

    if args.cmd == "generate":
        res = discover_corpus(args.corpus)
        pkg_root = emit_sysml2py(args.out, res, opts=EmitOptions(version=args.version))
        print(str(pkg_root))
        return 0

    return 2
