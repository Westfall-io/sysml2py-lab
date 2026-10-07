from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .discover import discover_corpus
from .codegen.emit import emit_sysml2py, EmitOptions
from .grammar.inputs import verify_inputs, record_manifest
from .grammar.spec import build_spec, write_spec
from .grammar.inline import inline_fragments
from .grammar.emit_tx import emit_tx
from .grammar.modifiers import build_modifier_model, modifiers_for_kind, write_modifiers
from .grammar.children import build_children_model, children_for_body, write_children
from .ir import parse_ir, ir_to_json, render_ir, ir_fidelity_summary
from .lexer import tokenize_sysml


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
    # `sysml2py-lab spec modifiers` subcommand
    sp_mod = sp.add_subparsers(dest="spec_cmd")
    sp_modifiers = sp_mod.add_parser("modifiers", help="Derive the per-kind modifier model from the inlined spec.")
    sp_modifiers.add_argument(
        "--dir", type=Path, default=Path("grammar_inputs"),
        help="Path to the vendored grammar-inputs directory (default: ./grammar_inputs)",
    )
    sp_modifiers.add_argument(
        "--kind", type=str, default=None,
        help="Restrict output to one prefix/kind (e.g. RefPrefix, PartUsage).",
    )
    sp_modifiers.add_argument(
        "--out", type=Path, default=Path("spec") / "relationships" / "modifiers.json",
        help="Output JSON path (default: ./spec/relationships/modifiers.json)",
    )
    sp_modifiers.add_argument(
        "--out-md", type=Path, default=Path("spec") / "relationships" / "modifiers.md",
        help="Output Markdown matrix path (default: ./spec/relationships/modifiers.md)",
    )
    # `sysml2py-lab spec children` subcommand
    sp_children = sp_mod.add_parser("children", help="Derive the per-container children/body-membership model from the spec.")
    sp_children.add_argument(
        "--dir", type=Path, default=Path("grammar_inputs"),
        help="Path to the vendored grammar-inputs directory (default: ./grammar_inputs)",
    )
    sp_children.add_argument(
        "--body", type=str, default=None,
        help="Restrict output to one body container (e.g. DefinitionBody, RequirementBody).",
    )
    sp_children.add_argument(
        "--out", type=Path, default=Path("spec") / "relationships" / "children.json",
        help="Output JSON path (default: ./spec/relationships/children.json)",
    )
    sp_children.add_argument(
        "--out-md", type=Path, default=Path("spec") / "relationships" / "children.md",
        help="Output Markdown matrix path (default: ./spec/relationships/children.md)",
    )
    sp_children.add_argument(
        "--out-parity", type=Path, default=Path("spec") / "reports" / "classes_parity.md",
        help="Output parity report path (default: ./spec/reports/classes_parity.md)",
    )

    tx = sub.add_parser("tx", help="Regenerate textX .tx grammar from the inlined spec.")
    tx.add_argument(
        "--dir", type=Path, default=Path("grammar_inputs"),
        help="Path to the vendored grammar-inputs directory (default: ./grammar_inputs)",
    )
    tx.add_argument(
        "--out", type=Path, default=Path("spec") / "tx",
        help="Output directory for regenerated .tx files (default: ./spec/tx)",
    )

    irp = sub.add_parser("ir", help="Parse a SysML text file into a loss-minimizing IR (JSON or re-rendered text).")
    irp.add_argument("file", type=Path, help="Path to a .sysml text file")
    irp.add_argument(
        "--model", type=Path, default=None,
        help="Path to issue-6 children.json used to classify kind/fidelity (default: repo spec/relationships/children.json)",
    )
    irp.add_argument(
        "--out", type=Path, default=None,
        help="Write IR JSON here instead of stdout (default: prints JSON to stdout)",
    )
    irp.add_argument(
        "--tokens", action="store_true",
        help="Emit the token stream instead of IR JSON.",
    )
    irp.add_argument(
        "--summary", action="store_true",
        help="Also print the per-file fidelity summary to stderr.",
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
        if getattr(args, "spec_cmd", None) == "modifiers":
            try:
                spec = build_spec(args.dir)
                # NOTE: build_modifier_model uses the spec's rule table with
                # fragments intact (the prefix chain is made of fragments).
                model = build_modifier_model(spec)
                if args.kind:
                    rec = modifiers_for_kind(model, args.kind)
                    print(__import__("json").dumps(rec, indent=2, sort_keys=True))
                else:
                    write_modifiers(model, args.out, args.out_md)
                    print(f"spec modifiers wrote {args.out} + {args.out_md} "
                          f"({len(model)} kinds)")
                return 0
            except Exception as e:
                print(f"spec modifiers FAILED: {e}", file=sys.stderr)
                return 1
        if getattr(args, "spec_cmd", None) == "children":
            try:
                spec = build_spec(args.dir)
                model = build_children_model(spec)
                if args.body:
                    rec = children_for_body(model, args.body)
                    print(__import__("json").dumps(rec, indent=2, sort_keys=True))
                else:
                    write_children(model, args.out, args.out_md, args.out_parity)
                    print(f"spec children wrote {args.out} + {args.out_md} + "
                          f"{args.out_parity} ({len(model['bodies'])} bodies)")
                return 0
            except Exception as e:
                print(f"spec children FAILED: {e}", file=sys.stderr)
                return 1
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

    if args.cmd == "tx":
        try:
            original = build_spec(args.dir)
            spec = inline_fragments(original)
            emit_tx(spec, args.out, original_spec=original)
            print(f"tx wrote {args.out} ({spec['counts']['total']} inlined rules)")
        except Exception as e:
            print(f"tx FAILED: {e}", file=sys.stderr)
            return 1
        return 0

    if args.cmd == "ir":
        try:
            text = args.file.read_text(encoding="utf-8")
            if args.tokens:
                for t in tokenize_sysml(text):
                    print(f"{t.start:>5}:{t.end:<5} {t.kind:<18} {t.text!r}")
                return 0
            root = parse_ir(text, model_path=str(args.model) if args.model else None)
            if args.summary:
                import json as _json
                print(_json.dumps(ir_fidelity_summary(root), indent=2), file=sys.stderr)
            payload = ir_to_json(root)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(__import__("json").dumps(payload, indent=2) + "\n", encoding="utf-8")
                print(f"ir wrote {args.out}")
            else:
                print(__import__("json").dumps(payload, indent=2))
            return 0
        except Exception as e:
            print(f"ir FAILED: {e}", file=sys.stderr)
            return 1

    return 2
