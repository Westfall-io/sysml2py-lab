"""Issue #11 — Regression harness: corpus replay, round-trip, determinism,
goldens, and coverage ratchet.

Composes the existing corpus / IR / codegen pieces into an enforceable,
CI-runnable regression loop over the whole SysML corpus:

  * ``replay``    — for every corpus file: text -> IR -> re-render (canonical
    compare) AND text -> IR -> generated classes -> dump (canonical compare),
    with a per-file fidelity class.
  * ``roundtrip`` — text -> IR -> Node.from_ir -> get_definition() -> dump()
    -> canonical compare (the direction the 72/266 get_definition() fidelity
    gap tracks).
  * ``determinism`` — generate twice, assert byte-identical; also assert the
    IR/regression outputs are independent of filesystem ordering.
  * ``goldens``   — committed snapshot of the generated package (and this
    report); CI diffs and requires an intentional refresh commit.
  * ``coverage``  — modelled/buildable/round-tripping rule counts against the
    baseline, with a ratchet that FAILS on regression (monotone non-decrease
    recorded per file kind coverage).

Naming: the module is intentionally *not* named ``regression`` (which could
shadow a test-runner concept) — it is ``regress``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from sysml2py_lab.normalize import canonical_equals

# ---------------------------------------------------------------------------
# Defaults / paths
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Repo root = two levels above this module's package dir.

    regress.py is at <root>/src/sysml2py_lab/regress.py → parents[2] = root.
    """
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _repo_root()
DEFAULT_CORPUS_DIR = REPO_ROOT / "corpus"
DEFAULT_GOLDENS_DIR = REPO_ROOT / "goldens"
DEFAULT_BASELINE = DEFAULT_GOLDENS_DIR / "coverage-baseline.json"


# ---------------------------------------------------------------------------
# Small data helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_dump(obj: Any, path: Path) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _fidelity_class(fidelity: dict) -> str:
    """Classify a file's fidelity into coarse buckets.

    ``opaque``  — every node opaque: nothing structurally modelled.
    ``partial`` — some nodes modelled.
    ``full``    — every node modelled (opaque == 0 and partial == 0).
    """
    modelled = int(fidelity.get("modelled", 0))
    partial = int(fidelity.get("partial", 0))
    opaque = int(fidelity.get("opaque", 0))
    if opaque == 0 and partial == 0:
        return "full"
    if modelled == 0 and opaque > 0:
        return "opaque"
    return "partial"


# ---------------------------------------------------------------------------
# File set (deterministic ordering — filesystem-agnostic)
# ---------------------------------------------------------------------------

def _manifest_files(corpus_dir: Path) -> list[str]:
    """Sorted manifest-relative paths (authoritative order, not rglob)."""
    manifest = _load_json(corpus_dir / "manifest.json", {"files": {}})
    return sorted(manifest.get("files", {}).keys())


def _iter_corpus(corpus_dir: Path) -> list[tuple[Path, str]]:
    """Return [(absolute_path, manifest_rel)] in manifest order."""
    out: list[tuple[Path, str]] = []
    for rel in _manifest_files(corpus_dir):
        p = (corpus_dir / rel).resolve()
        if p.exists():
            out.append((p, rel))
        else:
            # Keep the entry even if missing so the report can flag it.
            out.append((p, rel))
    return out


def _generated_pkg_src(pkg_root: Path) -> Path | None:
    """Given a generated package root, return the editable-style src/ dir to
    put on sys.path (``<root>/sysml2py/src``), or None if absent."""
    cand = pkg_root / "sysml2py" / "src"
    return cand if (cand / "sysml2py" / "__init__.py").exists() else None


def _ensure_pkg(pkg_root: str | Path | None) -> None:
    """Insert the generated package's src/ onto sys.path when given and not
    already importable."""
    if pkg_root is None:
        return
    src = _generated_pkg_src(Path(pkg_root))
    if src is None:
        return
    s = str(src)
    if s not in sys.path:
        sys.path.insert(0, s)


def _drop_pkg(pkg_root: str | Path | None) -> None:
    if pkg_root is None:
        return
    src = _generated_pkg_src(Path(pkg_root))
    if src is None:
        return
    s = str(src)
    if s in sys.path:
        sys.path.remove(s)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def replay(
    corpus_dir: Path,
    *,
    model_path: str | None = None,
    generated_pkg: str | Path | None = None,
) -> dict:
    """Run the full corpus loop; return a structured report.

    Per file, two stages:
      1. IR :  text -> parse_ir -> render_ir -> canonical-compare.
      2. AST:  text -> parse_ir -> Node.from_ir -> dump -> canonical-compare
               (needs the generated package importable on sys.path).

    ``model_path`` optionally overrides children.json used for fidelity.
    ``generated_pkg`` is a generated-package root whose ``sysml2py/src`` is
    put on sys.path so the AST stage can import the generated classes.
    """
    from sysml2py_lab.ir import parse_ir, render_ir

    _ensure_pkg(generated_pkg)
    results: list[dict] = []
    files = _iter_corpus(corpus_dir)
    try:
        for path, rel in files:
            if not path.exists():
                results.append({"file": rel, "ok": False, "error": "missing"})
                continue
            src = path.read_text(encoding="utf-8")
            entry: dict = {"file": rel}
            try:
                root = parse_ir(src, model_path=model_path)
            except Exception as exc:  # noqa: BLE001
                entry.update({"ok": False, "stage": "parse", "error": f"{exc.__class__.__name__}: {exc}"})
                results.append(entry)
                continue

            # fidelity class
            try:
                from sysml2py_lab.ir import ir_fidelity_summary

                summ = ir_fidelity_summary(root, source=src)
                entry["fidelity"] = {
                    "modelled": summ["node_counts"]["modelled"],
                    "partial": summ["node_counts"]["partial"],
                    "opaque": summ["node_counts"]["opaque"],
                }
                entry["fidelity_class"] = _fidelity_class(entry["fidelity"])
            except Exception:  # noqa: BLE001
                entry["fidelity_class"] = "partial"

            # stage 1: IR re-render canonical
            try:
                rendered = render_ir(root)
                entry["ir_roundtrip"] = canonical_equals(rendered, src)
            except Exception as exc:  # noqa: BLE001
                entry["ir_roundtrip"] = False
                entry.setdefault("errors", []).append(f"ir: {exc.__class__.__name__}: {exc}")

            # stage 2: generated-classes dump canonical (best-effort; needs pkg)
            try:
                import sysml2py  # type: ignore[import-not-found]

                j = _ir_to_json(root)
                tree = sysml2py.Node.from_ir(j)
                dumped = tree.dump()
                entry["ast_roundtrip"] = canonical_equals(dumped, src)
            except Exception as exc:  # noqa: BLE001
                entry["ast_roundtrip"] = None
                entry.setdefault("errors", []).append(f"ast: {exc.__class__.__name__}: {exc}")

            entry["ok"] = bool(entry.get("ir_roundtrip")) and entry.get("ir_roundtrip") is not None
            results.append(entry)
    finally:
        _drop_pkg(generated_pkg)

    summary = {
        "files": len(results),
        "pass": sum(1 for r in results if r.get("ok")),
        "fail": sum(1 for r in results if not r.get("ok")),
        "fidelity_classes": {
            cls: sum(1 for r in results if r.get("fidelity_class") == cls)
            for cls in ("full", "partial", "opaque")
        },
    }
    return {"summary": summary, "files": results}


def _ir_to_json(root) -> dict:
    from sysml2py_lab.ir import ir_to_json

    return ir_to_json(root)


# ---------------------------------------------------------------------------
# Round-trip (fidelity-gap tracked direction)
# ---------------------------------------------------------------------------

def roundtrip(
    corpus_dir: Path,
    *,
    model_path: str | None = None,
    generated_pkg: str | Path | None = None,
) -> dict:
    """text -> generated classes -> get_definition -> dump -> canonical.

    This is the direction the unresolved fidelity gap tracks.  Unlike replay
    stage 2 (which dumps the IR-lifted tree), roundtrip pushes through the
    semantic definition first.
    """
    from sysml2py_lab.ir import parse_ir

    _ensure_pkg(generated_pkg)
    results: list[dict] = []
    try:
        for path, rel in _iter_corpus(corpus_dir):
            if not path.exists():
                results.append({"file": rel, "ok": False, "error": "missing"})
                continue
            src = path.read_text(encoding="utf-8")
            entry: dict = {"file": rel}
            try:
                import sysml2py  # type: ignore[import-not-found]

                root = parse_ir(src, model_path=model_path)
                tree = sysml2py.Node.from_ir(_ir_to_json(root))
                gd = tree.get_definition()
                dumped = dump_from_definition(gd)
                entry["ok"] = canonical_equals(dumped, src)
            except Exception as exc:  # noqa: BLE001
                entry["ok"] = False
                entry["error"] = f"{exc.__class__.__name__}: {exc}"
            results.append(entry)
    finally:
        _drop_pkg(generated_pkg)

    summary = {
        "files": len(results),
        "pass": sum(1 for r in results if r.get("ok")),
        "fail": sum(1 for r in results if not r.get("ok")),
    }
    return {"summary": summary, "files": results}


def dump_from_definition(gd: dict) -> str:
    """Re-render a get_definition() dict through the generated Node.dump().

    Rebuilds a Node via ``Node.from_ir(gd)`` then ``dump()``.
    """
    import sysml2py  # type: ignore[import-not-found]

    return sysml2py.Node.from_ir(gd).dump()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def determinism(generate_args: list[str] | None = None, *, tmpdir: Path | None = None) -> dict:
    """Generate twice into fresh dirs and require byte-identical output.

    ``generate_args`` are CLI args to ``sysml2py-lab generate`` (e.g.
    ``["--out", <dir>]``); each run gets its own out dir.  Writes into a
    temp workspace unless ``tmpdir`` is given.
    """
    exe = _lab_cli()
    work = Path(tmpdir) if tmpdir else Path(tempfile.mkdtemp(prefix="sysml2py-regress-"))
    out_a = work / "a"
    out_b = work / "b"

    def _run(out: Path) -> subprocess.CompletedProcess[str]:
        out.mkdir(parents=True, exist_ok=True)
        cmd = [str(exe), "generate", "--out", str(out)]
        if generate_args:
            cmd = [str(exe), "generate", *generate_args]
            # ensure the out dir is set by caller-provided args
        return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=False)

    pa = _run(out_a)
    pb = _run(out_b)
    if pa.returncode != 0 or pb.returncode != 0:
        return {
            "ok": False,
            "error": f"generate failed: a={pa.returncode} b={pb.returncode}",
            "a": pa.stderr[-500:] if pa.stderr else pa.stdout[-500:],
            "b": pb.stderr[-500:] if pb.stderr else pb.stdout[-500:],
        }

    diffs = _diff_trees(out_a, out_b)
    return {"ok": not diffs, "diffs": diffs[:50], "a": str(out_a), "b": str(out_b)}


def _lab_cli() -> Path:
    """Locate the ``sysml2py-lab`` console script (venv-aware)."""
    import shutil

    exe = shutil.which("sysml2py-lab")
    if exe:
        return Path(exe)
    # fall back to the venv bin beside the running interpreter
    cand = Path(sys.executable).parent / "sysml2py-lab"
    return cand if cand.exists() else Path("sysml2py-lab")


def _diff_trees(a: Path, b: Path) -> list[str]:
    """Return differing relative paths between two identical-layout trees."""
    diffs: list[str] = []
    fa = {p.relative_to(a).as_posix(): p for p in a.rglob("*") if p.is_file()}
    fb = {p.relative_to(b).as_posix(): p for p in b.rglob("*") if p.is_file()}
    for rel, pa in fa.items():
        pb = fb.get(rel)
        if pb is None or pb.read_bytes() != pa.read_bytes():
            diffs.append(rel)
    for rel in set(fb) - set(fa):
        diffs.append(f"+{rel}")
    return sorted(diffs)


# ---------------------------------------------------------------------------
# Coverage report + ratchet
# ---------------------------------------------------------------------------

def coverage(
    corpus_dir: Path,
    *,
    model_path: str | None = None,
    baseline_path: Path | None = None,
) -> dict:
    """Measure modelled/buildable/round-tripping rule counts vs baseline.

    ``modelled``   — number of corpus node-kinds the relationship model can
                     build (the 1255/146/387 fidelity totals).
    ``buildable``  — number of node kinds assignable to a generated builder
                     class (via the generated IR_KIND_ALIASES surface).
    ``ir_roundtrip_files`` — count of corpus files whose IR re-render is
                     canonical-lossless.

    When ``baseline_path`` exists, the reported ``ratchet`` field compares
    against it and FAILS (False) on any regression (strict monotone
    non-decrease).  The baseline is a committed goldens artifact.
    """
    modelled, partial, opaque = _corpus_fidelity_totals(corpus_dir, model_path)
    buildable = _count_buildable(corpus_dir)
    ir_ok = _count_ir_roundtrip(corpus_dir, model_path)

    current = {
        "modelled": modelled,
        "partial": partial,
        "opaque": opaque,
        "buildable": buildable,
        "ir_roundtrip_files": ir_ok,
        "files": len(_manifest_files(corpus_dir)),
    }
    report = {"coverage": current}
    if baseline_path is not None and baseline_path.exists():
        base = _load_json(baseline_path, {}).get("coverage", {})
        regressions = _ratchet_regressions(base, current)
        report["baseline"] = base
        report["ratchet"] = {
            "pass": not regressions,
            "regressions": regressions,
        }
    else:
        report["ratchet"] = {"pass": True, "regressions": [], "note": "no baseline"}
    return report


def _corpus_fidelity_totals(corpus_dir: Path, model_path: str | None) -> tuple[int, int, int]:
    from sysml2py_lab.corpus import analyze_file

    m = p = o = 0
    for path, _rel in _iter_corpus(corpus_dir):
        if not path.exists():
            continue
        try:
            _kinds, summ = analyze_file(path, model_path=model_path)
        except Exception:  # noqa: BLE001, S112  # skip unparseable file in aggregate
            continue
        m += summ["node_counts"]["modelled"]
        p += summ["node_counts"]["partial"]
        o += summ["node_counts"]["opaque"]
    return m, p, o


def _count_buildable(corpus_dir: Path) -> int:
    """Count node-kinds that map to a generated builder class.

    Uses the generated ``IR_KIND_ALIASES`` vocabulary (buildable kinds) by
    scanning the corpus node_kinds from the manifest against the aliases.
    Falls back to 0 if the generated package is not importable.
    """
    try:
        import sysml2py  # type: ignore[import-not-found]

        aliases: dict[str, str] = getattr(sysml2py, "IR_KIND_ALIASES", {})
    except Exception:  # noqa: BLE001
        return 0

    manifest = _load_json(corpus_dir / "manifest.json", {"files": {}})
    seen: set[str] = set()
    for entry in manifest.get("files", {}).values():
        seen.update(entry.get("node_kinds", []))
    return sum(1 for k in seen if k in aliases and aliases[k] not in ("", "Unsupported"))


def _count_ir_roundtrip(corpus_dir: Path, model_path: str | None) -> int:
    from sysml2py_lab.ir import parse_ir, render_ir

    ok = 0
    for path, _rel in _iter_corpus(corpus_dir):
        if not path.exists():
            continue
        try:
            src = path.read_text(encoding="utf-8")
            root = parse_ir(src, model_path=model_path)
            if canonical_equals(render_ir(root), src):
                ok += 1
        except Exception:  # noqa: BLE001, S112  # skip unparseable file in aggregate
            continue
    return ok


def _ratchet_regressions(base: dict, current: dict) -> list[str]:
    """Return list of fields that regressed vs baseline (strict decrease)."""
    regs: list[str] = []
    for field in ("modelled", "partial", "opaque", "buildable", "ir_roundtrip_files"):
        b = int(base.get(field, 0) or 0)
        c = int(current.get(field, 0) or 0)
        if c < b:
            regs.append(f"{field}: {b} -> {c}")
    return regs


# ---------------------------------------------------------------------------
# Goldens
# ---------------------------------------------------------------------------

def goldens(
    corpus_dir: Path,
    goldens_dir: Path,
    *,
    model_path: str | None = None,
    generated_pkg: str | Path | None = None,
    write: bool = False,
) -> dict:
    """Manage the committed golden snapshot of the regression report.

    ``write=True`` refreshes the golden files from the current corpus state
    (an intentional refresh, done on a dedicated golden-update commit).
    ``write=False`` (default) diffs the current state against the committed
    goldens and reports drift.
    """
    goldens_dir.mkdir(parents=True, exist_ok=True)

    # golden 1: expanded per-file replay detail (modelled/partial/opaque)
    _ensure_pkg(generated_pkg)
    try:
        replay_report = replay(corpus_dir, model_path=model_path, generated_pkg=generated_pkg)
        files_golden = {
            r["file"]: {
                "fidelity": r.get("fidelity", {}),
                "fidelity_class": r.get("fidelity_class"),
                "ir_roundtrip": r.get("ir_roundtrip"),
            }
            for r in replay_report["files"]
        }
        files_gp = goldens_dir / "replay-fidelity.json"

        # golden 2: coverage snapshot
        cov_report = coverage(
            corpus_dir,
            model_path=model_path,
            baseline_path=DEFAULT_BASELINE,
        )
        cov_gp = goldens_dir / "coverage-baseline.json"
    finally:
        _drop_pkg(generated_pkg)

    changed: list[str] = []
    if write:
        _json_dump(files_golden, files_gp)
        _json_dump({"coverage": cov_report["coverage"]}, cov_gp)
        changed = [files_gp.name, cov_gp.name]
    else:
        if files_gp.exists():
            old = _load_json(files_gp, {})
            dr = _diff_dicts(old, files_golden)
            if dr:
                changed.append(f"replay-fidelity.json: {dr}")
        if cov_gp.exists():
            old_cov = _load_json(cov_gp, {}).get("coverage", {})
            dr = _diff_dicts(old_cov, cov_report["coverage"])
            if dr:
                changed.append(f"coverage-baseline.json: {dr}")

    return {"ok": not changed, "changed": changed, "dir": str(goldens_dir)}


def _diff_dicts(a: dict, b: dict, _prefix: str = "") -> list[str]:
    """Return a flat list of 'path: A=x B=y' lines for differing leaves."""
    out: list[str] = []
    keys = sorted(set(a) | set(b))
    for k in keys:
        key = f"{_prefix}.{k}" if _prefix else k
        if k not in a:
            out.append(f"{key}: (absent) -> {b[k]!r}")
        elif k not in b:
            out.append(f"{key}: {a[k]!r} -> (absent)")
        elif isinstance(a[k], dict) and isinstance(b[k], dict):
            out.extend(_diff_dicts(a[k], b[k], key))
        elif a[k] != b[k]:
            out.append(f"{key}: {a[k]!r} -> {b[k]!r}")
    return out
