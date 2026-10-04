from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .discover import discover_corpus
from .codegen.emit import emit_sysml2py, EmitOptions
from .grammar.inputs import verify_inputs, record_manifest
from .grammar.spec import build_spec, write_spec


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sysml2py-lab", description="Generate sysml2py from a SysML v2 corpus (MVP).")

    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="Scan corpus and print discovered statement keyword counts.")
    d.add_argument("corpus", type=Path)

    g = sub.add_parser("generate", help="Discover + emit a generated sysml2py package.")
    g.add_argument("corpus", type=Path)
    g.add_argument("--out", type=Path, default=Path("out"))
    g.add_argument("--version", default="0.0.0")

    iv = sub.add_parser("inputs", help="Grammar input management (vendor / verify).")
    iv_sub = iv.add_subparsers(dest="inputs_cmd", required=True)
    iv_verify = iv_sub.add_parser("verify", help="Read-only verify vendored grammar inputs against the recorded manifest.")
    iv_verify.add_argument(
        "--dir", type=Path, default=Path("grammar_inputs"),
        help="Path to the vendored grammar-inputs directory (default: ./grammar_inputs)",
    )
    iv_record = iv_sub.add_parser("record", help="Record sha256 checksums for all grammar inputs into the manifest (explicit bless).")
    iv_record.add_argument(
        "--dir", type=Path, default=Path("grammar_inputs"),
        help="Path to the vendored grammar-inputs directory (default: ./grammar_inputs)",
    )

    sp = sub.add_parser("spec", help="Parse the Xtext grammar inputs into language_spec.json.")
    sp.add_argument(
        "--dir", type=Path, default=Path("grammar_inputs"),
        help="Path to the vendored grammar-inputs directory (default: ./grammar_inputs)",
    )
    sp.add_argument(
        "--out", type=Path, default=Path("spec") / "language_spec.json",
        help="Output path (default: ./spec/language_spec.json)",
    )

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

    if args.cmd == "inputs":
        if args.inputs_cmd == "verify":
            try:
                manifest = verify_inputs(args.dir)
            except Exception as e:
                print(f"inputs verify FAILED: {e}", file=sys.stderr)
                return 1
            print("inputs verify OK:")
            for rel in sorted(manifest.entries):
                print(f"  {rel}  sha256={manifest.entries[rel][:16]}...")
            return 0
        if args.inputs_cmd == "record":
            try:
                manifest = record_manifest(args.dir)
            except Exception as e:
                print(f"inputs record FAILED: {e}", file=sys.stderr)
                return 1
            print(f"inputs record OK ({len(manifest.entries)} files):")
            for rel in sorted(manifest.entries):
                print(f"  {rel}  sha256={manifest.entries[rel][:16]}...")
            return 0

    if args.cmd == "spec":
        try:
            spec = build_spec(args.dir)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_spec(spec, args.out)
        except Exception as e:
            print(f"spec FAILED: {e}", file=sys.stderr)
            return 1
        print(f"spec wrote {args.out} ({spec['counts']['total']} rules)")
        print(f"  rules={spec['counts']['rules']} fragments={spec['counts']['fragments']} "
              f"terminals={spec['counts']['terminals']} enums={spec['counts']['enums']}")
        return 0

    return 2
