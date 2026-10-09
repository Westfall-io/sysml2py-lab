from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .codegen.emit import EmitOptions, emit_sysml2py
from .discover import discover_corpus
from .grammar.children import build_children_model, children_for_body, write_children
from .grammar.emit_tx import emit_tx
from .grammar.inline import inline_fragments
from .grammar.inputs import record_manifest, verify_inputs
from .grammar.modifiers import build_modifier_model, modifiers_for_kind, write_modifiers
from .grammar.spec import build_spec, write_spec
from .ir import ir_fidelity_summary, ir_to_json, parse_ir, render_ir
from .lexer import tokenize_sysml


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sysml2py-lab", description="Generate sysml2py from a SysML v2 corpus (MVP).")

    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="Scan corpus and print discovered statement keyword counts.")
    d.add_argument("corpus", type=Path)

    g = sub.add_parser(
        "generate",
        help="Generate a sysml2py package with AST classes from the spec + corpus.",
    )
    g.add_argument("--out", type=Path, default=Path("out"))
    g.add_argument("--version", default="0.0.0")
    g.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting an existing generated package root (default: refuse).",
    )
    g.add_argument(
        "--generated-at",
        default=None,
        help="Inject a timestamp into the provenance stamp.  Default: fixed "
        "SOURCE_DATE_EPOCH-style value so two runs are byte-identical.",
    )
    g.add_argument(
        "--generated-at-now",
        action="store_true",
        help="Use the current time in provenance (explicit opt-in to nondeterminism).",
    )

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

    cp = sub.add_parser("corpus", help="Manage the SysML corpus: add / verify / stats.")
    cp_sub = cp.add_subparsers(dest="corpus_cmd", required=True)
    cp_add = cp_sub.add_parser("add", help="Add a .sysml file to the corpus and update the manifest.")
    cp_add.add_argument("file", type=Path, help="Path to the .sysml file to add")
    cp_add.add_argument("--corpus", type=Path, default=Path("corpus"), help="Corpus root (default: ./corpus)")
    cp_add.add_argument("--source", default="local", help="Source name for provenance")
    cp_add.add_argument("--source-url", default="", help="Source URL for provenance")
    cp_add.add_argument("--license", default="", help="License string")
    cp_add.add_argument("--dest", default="local", help="Destination subdir under the corpus root")
    cp_add.add_argument("--function", default=None, help="Source test function name (provenance)")
    cp_add.add_argument("--model", type=Path, default=None, help="children.json path")
    cp_verify = cp_sub.add_parser("verify", help="Verify sha256 + parseability of every corpus file.")
    cp_verify.add_argument("--corpus", type=Path, default=Path("corpus"), help="Corpus root (default: ./corpus)")
    cp_verify.add_argument("--model", type=Path, default=None, help="children.json path")
    cp_stats = cp_sub.add_parser("stats", help="Report node-kind coverage vs the relationship model.")
    cp_stats.add_argument("--corpus", type=Path, default=Path("corpus"), help="Corpus root (default: ./corpus)")
    cp_stats.add_argument("--model", type=Path, default=None, help="children.json path")

    rg = sub.add_parser("regress", help="Issue #11 regression harness: replay / roundtrip / determinism / goldens / coverage.")
    rg_sub = rg.add_subparsers(dest="regress_cmd", required=True)
    rg.add_argument("--corpus", type=Path, default=None, help="Corpus root (default: repo ./corpus)")
    rg.add_argument("--model", type=Path, default=None, help="children.json path")
    rg.add_argument("--generated", type=Path, default=None, help="Generated package root (optional; enables the AST/roundtrip stages)")
    rg_replay = rg_sub.add_parser("replay", help="Replay every corpus file: IR + generated-classes canonical compare.")
    rg_rt = rg_sub.add_parser("roundtrip", help="text -> generated classes -> get_definition -> dump -> canonical compare.")
    rg_det = rg_sub.add_parser("determinism", help="Generate twice; assert byte-identical output.")
    rg_gold = rg_sub.add_parser("goldens", help="Check (or refresh, --write) committed golden snapshots.")
    rg_gold.add_argument("--write", action="store_true", help="Refresh the goldens (intentional update commit).")
    rg_gold.add_argument("--goldens-dir", type=Path, default=Path("goldens"), help="Goldens root (default: ./goldens)")
    rg_cov = rg_sub.add_parser("coverage", help="Coverage report vs the baseline; fails on regression.")
    rg_cov.add_argument("--baseline", type=Path, default=None, help="Coverage baseline JSON (default: ./goldens/coverage-baseline.json)")

    args = p.parse_args(argv)

    if args.cmd == "discover":
        res = discover_corpus(args.corpus)
        print(f"files_scanned={res.files_scanned}")
        for k, v in sorted(res.statement_prefix_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{k}: {v}")
        if res.node_kind_counts:
            print("node_kinds (relationship-aware):")
            for k, v in sorted(res.node_kind_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {k}: {v}")
        return 0

    if args.cmd == "generate":
        # Deterministic by default: fixed generated-at so two runs are
        # byte-identical (acceptance criterion).  --generated-at-now opts
        # into wall-clock provenance explicitly.
        generated_at = args.generated_at
        if generated_at is None and not args.generated_at_now:
            generated_at = "1970-01-01T00:00:00Z"
        elif generated_at is None:
            import datetime as _dt

            generated_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        pkg_root = emit_sysml2py(
            args.out,
            opts=EmitOptions(
                version=args.version,
                generated_at=generated_at,
                overwrite=args.overwrite,
            ),
        )
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
            if args.model is not None and not Path(args.model).exists():
                print(f"warning: --model {args.model} not found; "
                      "classifying via static fallback table", file=sys.stderr)
            if args.summary:
                import json as _json
                print(_json.dumps(ir_fidelity_summary(root, source=text),
                                  indent=2), file=sys.stderr)
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

    if args.cmd == "corpus":
        import json as _json

        from .corpus import add_file, corpus_stats, verify_corpus
        try:
            if args.corpus_cmd == "add":
                rel = add_file(
                    args.corpus, args.file,
                    source=args.source, source_url=args.source_url,
                    license=args.license, dest_subdir=args.dest,
                    model_path=str(args.model) if args.model else None,
                    function=args.function,
                )
                print(f"corpus add OK: {rel}")
                return 0
            if args.corpus_cmd == "verify":
                report = verify_corpus(args.corpus, model_path=str(args.model) if args.model else None)
                print(f"corpus verify: {report['checked']} files checked, {len(report['ok'])} ok")
                for e in report["errors"]:
                    print(f"  ERROR {e}")
                return 1 if report["errors"] else 0
            if args.corpus_cmd == "stats":
                stats = corpus_stats(args.corpus, model_path=str(args.model) if args.model else None)
                print(f"corpus stats: {stats['files']} files")
                print(f"model_loaded: {stats['model_loaded']}")
                print("files containing each kind:")
                for k, v in stats["files_with_kind"].items():
                    print(f"  {k:16s} {v}")
                print("kinds with ZERO corpus coverage:")
                for k in stats["kinds_zero_coverage"]:
                    print(f"  {k}")
                print("fidelity totals: " + _json.dumps(stats["fidelity_totals"]))
                return 0
        except Exception as e:
            print(f"corpus {args.corpus_cmd} FAILED: {e}", file=sys.stderr)
            return 1

    if args.cmd == "regress":
        import json as _json

        from . import regress as rg

        corpus_dir = args.corpus.resolve() if args.corpus else rg.DEFAULT_CORPUS_DIR
        model_path = str(args.model) if args.model else None
        gen_pkg = str(args.generated.resolve()) if args.generated else None
        cmd = args.regress_cmd
        try:
            if cmd == "replay":
                report = rg.replay(corpus_dir, model_path=model_path, generated_pkg=gen_pkg)
                s = report["summary"]
                print(f"replay: {s['files']} files, {s['pass']} pass, {s['fail']} fail")
                print("fidelity classes: " + _json.dumps(s["fidelity_classes"]))
                for f in report["files"]:
                    if not f.get("ok"):
                        print(f"  FAIL {f['file']}: {f.get('error','')} ir_rt={f.get('ir_roundtrip')}")
                return 0 if not report["summary"]["fail"] else 1
            if cmd == "roundtrip":
                report = rg.roundtrip(corpus_dir, model_path=model_path, generated_pkg=gen_pkg)
                s = report["summary"]
                print(f"roundtrip: {s['files']} files, {s['pass']} pass, {s['fail']} fail")
                for f in report["files"]:
                    if not f.get("ok"):
                        print(f"  FAIL {f['file']}: {f.get('error','')}")
                return 0 if not report["summary"]["fail"] else 1
            if cmd == "determinism":
                report = rg.determinism()
                print(f"determinism: {'OK' if report['ok'] else 'FAIL'}")
                for d in report.get("diffs", []):
                    print(f"  diff {d}")
                if not report.get("ok"):
                    print("error:", report.get("error"))
                return 0 if report.get("ok") else 1
            if cmd == "goldens":
                gold_dir = args.goldens_dir.resolve() if args.goldens_dir else rg.DEFAULT_GOLDENS_DIR
                report = rg.goldens(corpus_dir, gold_dir, model_path=model_path,
                                    generated_pkg=gen_pkg, write=args.write)
                if args.write:
                    print(f"goldens refreshed -> {report['dir']}")
                    return 0
                print(f"goldens: {'CLEAN' if report['ok'] else 'DIFF'}")
                for c in report.get("changed", []):
                    print(f"  {c}")
                return 0 if report.get("ok") else 1
            if cmd == "coverage":
                base = args.baseline.resolve() if args.baseline else rg.DEFAULT_BASELINE
                rg._ensure_pkg(gen_pkg)
                try:
                    report = rg.coverage(corpus_dir, model_path=model_path, baseline_path=base)
                finally:
                    rg._drop_pkg(gen_pkg)
                print("coverage: " + _json.dumps(report["coverage"]))
                if "baseline" in report:
                    rat = report["ratchet"]
                    print(f"ratchet: {'PASS' if rat['pass'] else 'REGRESSION'}")
                    for r in rat.get("regressions", []):
                        print(f"  {r}")
                    return 0 if rat["pass"] else 1
                print("ratchet: PASS (no baseline yet)")
                return 0
        except Exception as e:
            print(f"regress {cmd} FAILED: {e}", file=sys.stderr)
            return 1

    return 2
