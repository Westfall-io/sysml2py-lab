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
from .ir import ir_fidelity_summary, ir_to_json, parse_ir
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
    cp_add.add_argument("--allow-invalid", action="store_true",
                        help="Issue #12: allow adding a file that windtrader rejects (WIP escape). "
                             "Default: REFUSE to add a file lacking a passing windtrader verdict (AC#3).")
    cp_add.add_argument("--offline", type=Path, default=None,
                        help="Issue #12: replay committed windtrader fixtures (no JVM needed).")
    cp_add.add_argument("--windtrader-version", default="0.2.0",
                        help="windtrader-java version to validate against (default: 0.2.0).")
    cp_verify = cp_sub.add_parser("verify", help="Verify sha256 + parseability of every corpus file.")
    cp_verify.add_argument("--corpus", type=Path, default=Path("corpus"), help="Corpus root (default: ./corpus)")
    cp_verify.add_argument("--model", type=Path, default=None, help="children.json path")
    cp_stats = cp_sub.add_parser("stats", help="Report node-kind coverage vs the relationship model.")
    cp_stats.add_argument("--corpus", type=Path, default=Path("corpus"), help="Corpus root (default: ./corpus)")
    cp_stats.add_argument("--model", type=Path, default=None, help="children.json path")

    rg = sub.add_parser("regress", help="Issue #11 regression harness: replay / roundtrip / determinism / goldens / coverage.")
    rg_sub = rg.add_subparsers(dest="regress_cmd", required=True)
    _rg_common = [
        ("--corpus", "Corpus root (default: repo ./corpus)"),
        ("--model", "children.json path"),
        ("--generated", "Generated package root (optional; enables the AST/roundtrip stages)"),
    ]
    for name, help_ in (("replay", "Replay every corpus file: IR + generated-classes canonical compare."),
                        ("roundtrip", "text -> generated classes -> get_definition -> dump -> canonical compare.")):
        rsub = rg_sub.add_parser(name, help=help_)
        for flag, h in _rg_common:
            rsub.add_argument(flag, type=Path, default=None, help=h)
    rg_sub.add_parser("determinism", help="Generate twice; assert byte-identical output.")
    rg_gold = rg_sub.add_parser("goldens", help="Check (or refresh, --write) committed golden snapshots.")
    rg_gold.add_argument("--write", action="store_true", help="Refresh the goldens (intentional update commit).")
    rg_gold.add_argument("--goldens-dir", type=Path, default=Path("goldens"), help="Goldens root (default: ./goldens)")
    for flag, h in _rg_common:
        rg_gold.add_argument(flag, type=Path, default=None, help=h)
    rg_cov = rg_sub.add_parser("coverage", help="Coverage report vs the baseline; fails on regression.")
    rg_cov.add_argument("--baseline", type=Path, default=None, help="Coverage baseline JSON (default: ./goldens/coverage-baseline.json)")
    for flag, h in _rg_common:
        rg_cov.add_argument(flag, type=Path, default=None, help=h)

    vl = sub.add_parser("validate", help="Issue #12 windtrader gate: validate SysML text/corpus + round-trip output.")
    vl.add_argument("target", type=Path, help="A .sysml file, or a corpus dir (has manifest.json).")
    vl.add_argument("--generated", type=Path, default=None,
                    help="Generated package root (enables round-trip-output validation; default: inputs only).")
    vl.add_argument("--version", default=None,
                    help="windtrader-java version (default: 0.2.0).")
    vl.add_argument("--record", type=Path, default=None,
                    help="Record live verdicts and write a fixtures JSON here (e.g. corpus/validate-fixtures.json).")
    vl.add_argument("--offline", type=Path, default=None,
                    help="Replay committed fixtures from this JSON path (no JVM needed).")
    vl.add_argument("--manifest", action="store_true",
                    help="Write input verdicts into corpus/manifest.json windtrader field.")
    vl.add_argument("--force", action="store_true",
                    help="Write --manifest/--record even when the gate fails "
                         "(deliberate re-baseline after a legitimate verdict change).")

    args = p.parse_args(argv)

    # r4 W2-1/W2-2: reject incoherent validate flag combos BEFORE any work
    # or on-disk writes, so a failing run has no partial side effects.
    if args.cmd == "validate":
        if args.record is not None and args.offline is not None:
            print("validate: --record and --offline are mutually exclusive "
                  "(recording writes fresh fixtures; offline replays them)",
                  file=sys.stderr)
            return 2
        if args.record is not None and args.generated is None:
            print("validate: --record requires --generated (round-trip "
                  "digests are part of the fixture contract)", file=sys.stderr)
            return 2

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
                # Issue #12 AC#3: a new example cannot enter the corpus
                # without a passing windtrader verdict — unless the caller
                # explicitly opts in (--allow-invalid, WIP escape).
                from .validate import windtrader as vwt
                version = args.windtrader_version or vwt.DEFAULT_VERSION
                recorded = None
                if args.offline is not None:
                    # r4 W2-4: a NEW file's digest is absent from committed
                    # fixtures by construction, so --offline cannot succeed
                    # for corpus add. Warn and refuse up front instead of
                    # reaching the dead-end AdapterError below.
                    print(f"corpus add REFUSED: --offline cannot validate a NEW "
                          f"file (its digest is not in {args.offline}) — run "
                          f"live (no --offline) to add it", file=sys.stderr)
                    return 1
                text = args.file.read_text(encoding="utf-8")
                try:
                    verb = vwt.validate_text(text, version=version,
                                             recorded=recorded)
                except vwt.AdapterError as exc:
                    # r4 W1-3: --allow-invalid does NOT bypass an adapter
                    # error (only a non-valid verdict) — don't advertise a
                    # dead escape hatch.
                    print(f"corpus add REFUSED: windtrader could not validate "
                          f"({exc}) — adapter failure, not a verdict", file=sys.stderr)
                    return 1
                if verb.status != "valid":
                    if args.allow_invalid:
                        print(f"corpus add WARNING: {args.file} is invalid per windtrader "
                              f"({verb.exit_code}) but --allow-invalid given", file=sys.stderr)
                    else:
                        diag = (verb.diagnostics or [""])[0]
                        print(f"corpus add REFUSED: {args.file} does not pass windtrader "
                              f"validation (AC#3): {verb.status} {diag}", file=sys.stderr)
                        return 1
                rel = add_file(
                    args.corpus, args.file,
                    source=args.source, source_url=args.source_url,
                    license=args.license, dest_subdir=args.dest,
                    model_path=str(args.model) if args.model else None,
                    function=args.function,
                )
                # Thread the verdict so the file is immediately baselined in
                # the manifest (r1 W1-6; r2 W1-5; r3 W1-D): stamp the
                # windtrader field with the ACTUAL verdict — valid OR
                # invalid.  An invalid-but-allowed file is a legitimate
                # tracked-WIP baseline (exactly what the 33 committed
                # invalid entries look like); leaving it "unverified" would
                # make the very next `validate corpus` reject it.
                # The fixture file is NOT touched here — corpus add has no
                # round-trip output, so a half fixture would guarantee drift
                # (r2 W1-5); re-record deliberately with
                # `sysml2py-lab validate --record`.
                try:
                    mpath = args.corpus / "manifest.json"
                    m = _json.loads(mpath.read_text(encoding="utf-8"))
                    m["files"][rel]["windtrader"] = {
                        "status": verb.status,
                        "version": version,
                        "exit_code": verb.exit_code,
                    }
                    mpath.write_text(_json.dumps(m, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    print(f"corpus add WARNING: file added but could not stamp "
                          f"manifest windtrader field: {exc}", file=sys.stderr)
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

        corpus_dir = args.corpus.resolve() if getattr(args, "corpus", None) else rg.DEFAULT_CORPUS_DIR
        model_path = str(args.model) if getattr(args, "model", None) else None
        gen_pkg = str(args.generated.resolve()) if getattr(args, "generated", None) else None
        cmd = args.regress_cmd
        try:
            if cmd == "replay":
                report = rg.replay(corpus_dir, model_path=model_path, generated_pkg=gen_pkg)
                s = report["summary"]
                print(f"replay: {s['files']} files, {s['pass']} pass, {s['fail']} fail")
                print("fidelity classes: " + _json.dumps(s["fidelity_classes"]))
                for f in report["files"]:
                    if not f.get("ok"):
                        errs = "; ".join(f.get("errors", [])) or f.get("error", "")
                        print(f"  FAIL {f['file']}: {errs} ir_rt={f.get('ir_roundtrip')} ast_rt={f.get('ast_roundtrip')}")
                return 0 if not report["summary"]["fail"] else 1
            if cmd == "roundtrip":
                report = rg.roundtrip(corpus_dir, model_path=model_path, generated_pkg=gen_pkg)
                s = report["summary"]
                print(f"roundtrip: {s['files']} files, {s['pass']} pass, {s['fail']} fail")
                for f in report["files"]:
                    if not f.get("ok"):
                        errs = "; ".join(f.get("errors", [])) or f.get("error", "")
                        print(f"  FAIL {f['file']}: {errs}")
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

    if args.cmd == "validate":
        from .validate import corpus as vcor
        from .validate import windtrader as vwt

        try:
            version = args.version or vwt.DEFAULT_VERSION
            # offline / fixture replay mode
            recorded = None
            if args.offline is not None:
                recorded = vwt.load_fixtures(args.offline)

            target = args.target.resolve()
            is_dir = target.is_dir()
            verdicts: dict = {}
            v = None

            if is_dir:
                verdicts = vcor.compute_verdicts(
                    target, version=version,
                    generated_pkg=args.generated,
                    recorded=recorded,
                )
                summary = vcor.summarize(verdicts)

                print(f"validate: {summary['files']} files")
                print(f"  input_valid: {summary['input_valid']}")
                if summary["roundtrip_present"]:
                    print(f"  roundtrip_valid: {summary['roundtrip_valid']} / "
                          f"{summary['roundtrip_present']}")
                print(f"  input_invalid: {summary['input_invalid']}")
                if summary["roundtrip_invalid"]:
                    print(f"  roundtrip_invalid: {summary['roundtrip_invalid']}")
                print(f"  input_adapter_errors: {summary['input_adapter_errors']}")
                if summary["roundtrip_adapter_errors"]:
                    print(f"  roundtrip_adapter_errors: {summary['roundtrip_adapter_errors']}")
                if summary["roundtrip_emit_errors"]:
                    print(f"  roundtrip_emit_errors: {summary['roundtrip_emit_errors']}")
                for rel, e in sorted(verdicts.items()):
                    st = e.get("input_status")
                    rt_bad = e.get("roundtrip_status") not in (None, "valid")
                    if st != "valid" or rt_bad:
                        tag = f"input={st}"
                        if "roundtrip_status" in e:
                            tag += f" rt={e.get('roundtrip_status')}"
                        # r2 W1-3: on a round-trip failure show the ROUND-TRIP
                        # diagnostics (what the generator emitted wrong), not
                        # the input's.
                        if e.get("roundtrip_status") not in (None, "valid", "emit_error", "adapter_error"):
                            diag = (e.get("roundtrip_diagnostics") or [""])[0]
                        else:
                            diag = (e.get("diagnostics") or [""])[0]
                        print(f"  FAIL {rel}: {tag} {diag}")
            else:
                if args.generated is not None:
                    print("validate: --generated applies only to corpus scope; "
                          "ignored for a single file", file=sys.stderr)
                if args.record is not None or args.manifest:
                    print("validate: --record/--manifest apply only to corpus scope; "
                          "ignored for a single file", file=sys.stderr)
                text = target.read_text(encoding="utf-8")
                try:
                    v = vwt.validate_text(text, version=version, recorded=recorded)
                except vwt.AdapterError as exc:
                    # r3 W2-J: an expected condition (offline digest miss /
                    # no JVM), not a crash — report it like the directory
                    # path does.
                    print(f"GATE: adapter_error — {exc}", file=sys.stderr)
                    return 1
                # Single-file summary goes through the SAME summarize() used
                # for corpus scope (r3 W1-A): the r2 blocker was this
                # summary being hand-duplicated and drifting out of sync.
                summary = vcor.summarize({
                    str(target): {
                        "input_status": v.status,
                        "input_exit": v.exit_code,
                        "diagnostics": v.diagnostics,
                    }
                })
                print(f"validate: status={v.status} exit={v.exit_code}")
                diag = (v.diagnostics or [""])[0]
                if diag:
                    print("  " + diag)

            # Gate (ratchet, issue-#12 protocol §6):
            #  * directory scope — fail on: adapter_errors (tooling broke),
            #    ratchet regressions (valid → invalid/adapter, input AND
            #    round-trip), round-trip regressions (input valid but
            #    generator output invalid — B1), emit_errors (generator
            #    crashed — B5), and unbaselined/new files (AC#3, B2).
            #    Baseline invalids are tracked WIP, NOT gate failures.
            #  * single-file scope — strict: any invalid/adapter fails.
            failed = False
            if summary["input_adapter_errors"] > 0 or summary["roundtrip_adapter_errors"] > 0:
                print("GATE: adapter_errors present — tooling failure, not a verdict", file=sys.stderr)
                failed = True
            if is_dir:
                regressions = vcor.ratchet_regressions(target, verdicts)
                if regressions:
                    print("GATE: ratchet regressions", file=sys.stderr)
                    for r in regressions:
                        print(f"  {r}", file=sys.stderr)
                    failed = True
                rt_regressions = vcor.roundtrip_regressions(verdicts)
                if rt_regressions:
                    print("GATE: round-trip (AC#2) regressions", file=sys.stderr)
                    for r in rt_regressions:
                        print(f"  {r}", file=sys.stderr)
                    failed = True
                emit_errs = vcor.emit_error_files(verdicts)
                if emit_errs:
                    print("GATE: generator emit_errors", file=sys.stderr)
                    for r in emit_errs:
                        print(f"  {r}", file=sys.stderr)
                    failed = True
                unbaselined = vcor.unbaselined_files(target, verdicts)
                if unbaselined:
                    print("GATE: unbaselined files without a passing verdict (AC#3)", file=sys.stderr)
                    for u in unbaselined:
                        print(f"  {u}", file=sys.stderr)
                    failed = True
                if summary["input_invalid"] > 0:
                    print(f"  (note: {summary['input_invalid']} baseline invalid verdicts are tracked WIP — see validate/protocol.md §5)")
            else:
                if v is not None and v.status == "invalid":
                    print("GATE: invalid verdicts present", file=sys.stderr)
                    failed = True

            # On-disk writes only AFTER the gate passes (r2 W1-2): a failing
            # run must never clobber the committed baseline or trust anchor.
            # --force overrides for a deliberate re-baseline (r3 W2-E).
            if args.cmd == "validate" and is_dir and (not failed or args.force):
                if args.manifest:
                    vcor.update_manifest_verdicts(target, verdicts)
                if args.record is not None and args.offline is None:
                    # --record without --generated was already rejected at
                    # parse time (r4 W2-1).
                    vcor.write_fixture_file(
                        target, verdicts,
                        dest=args.record,
                        generated_pkg=args.generated,
                        version=version,
                    )
            return 1 if failed else 0
        except Exception as e:
            import traceback
            print(f"validate FAILED: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 1

    return 2
