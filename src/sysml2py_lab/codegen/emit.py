"""Deterministic AST-class codegen (issue #9).

Turns the committed spec artifacts (language_spec.json + children.json +
modifiers.json + corpus manifest) into a generated ``sysml2py`` package with
per-rule AST node classes, a membership-dispatch table, and a provenance
stamp.

Determinism: the generator reads only committed metadata; ``generated_at`` is
injected (the CLI defaults to a fixed SOURCE_DATE_EPOCH-style value so two
runs are byte-identical).  ``--no-clobber``-style safety: the CLI refuses to
overwrite an existing package root without an explicit flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..discover import DiscoveryResult
from .model import IR_KIND_ALIASES, CodegenModel


@dataclass(frozen=True)
class EmitOptions:
    """Options controlling generated sysml2py output."""

    version: str = "0.0.0"
    package_name: str = "sysml2py"
    generated_at: str | None = None
    repo_root: Path | None = None
    overwrite: bool = False


def _template_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(enabled_extensions=()),
        keep_trailing_newline=True,
    )
    return env


def _simplify_body(body: dict | None) -> str:
    """One-line description of a rule body for docstrings."""
    if not body:
        return ""
    kind = body.get("kind", "")
    items = body.get("items") or body.get("choices") or []
    names = []
    for it in items:
        if it.get("kind") == "call":
            names.append(it.get("name", "?"))
        elif it.get("kind") == "lit":
            names.append(repr(it.get("value", "")))
        elif it.get("kind") == "assign":
            names.append(it.get("name", "?"))
        elif it.get("kind") in ("seq", "alt"):
            names.append(f"<{it.get('kind')}>")
    return f"{kind} " + " ".join(names) if names else kind


def _emit_ast_classes(model: CodegenModel) -> str:
    env = _template_env()
    kinds = model.node_kinds()
    dispatch = model.dispatch_map()
    # topologically-ordered unique dispatch values (so imports are stable)
    dispatch_values: list[str] = []
    seen: set[str] = set()
    for table in dispatch.values():
        for child in table.values():
            if child not in seen:
                seen.add(child)
                dispatch_values.append(child)
    return env.get_template("ast_classes.py.j2").render(
        header=_HEADER,
        node_kinds=kinds,
        simplify_body=_simplify_body,
        ir_kind_aliases=IR_KIND_ALIASES,
    )


def _emit_ast_dispatch(model: CodegenModel) -> str:
    env = _template_env()
    dispatch = model.dispatch_map()
    dispatch_values: list[str] = []
    seen: set[str] = set()
    for table in dispatch.values():
        for child in table.values():
            if child not in seen:
                seen.add(child)
                dispatch_values.append(child)
    return env.get_template("ast_dispatch.py.j2").render(
        header=_HEADER,
        dispatch_map=dispatch,
        dispatch_values=dispatch_values,
        ir_kind_aliases=IR_KIND_ALIASES,
    )


def _emit_provenance(model: CodegenModel, opts: EmitOptions, repo_root: Path, generated_at: str) -> str:
    env = _template_env()
    prov = model.provenance(repo_root, opts.version, generated_at)
    return env.get_template("provenance.py.j2").render(
        header=_HEADER,
        provenance=prov,
    )


def emit_sysml2py(
    out_dir: Path,
    discovery: DiscoveryResult | None = None,
    opts: EmitOptions | None = None,
    model: CodegenModel | None = None,
) -> Path:
    """Write a generated `sysml2py` package to `out_dir/sysml2py/`.

    Returns the path to the generated package root.  Does NOT overwrite an
    existing package without ``opts.overwrite``.
    """
    opts = opts or EmitOptions()
    out_dir = out_dir.expanduser().resolve()
    # emit.py is at <root>/src/sysml2py_lab/codegen/emit.py → parents[3] = root
    repo_root = opts.repo_root or Path(__file__).resolve().parents[3]
    model = model or CodegenModel.load(repo_root)
    # Deterministic provenance by default: a fixed generated-at stamp so two
    # runs are byte-identical.  Callers may inject an explicit timestamp.
    generated_at = opts.generated_at if opts.generated_at is not None else "1970-01-01T00:00:00Z"
    pkg_root = out_dir / opts.package_name
    src_pkg = pkg_root / "src" / opts.package_name

    if src_pkg.exists() and not opts.overwrite:
        raise FileExistsError(
            f"{src_pkg} already exists — pass overwrite=True (CLI: --overwrite) "
            f"to regenerate in place"
        )

    env = _template_env()

    # Create directories
    src_pkg.mkdir(parents=True, exist_ok=True)

    # AST classes + dispatch + provenance
    (src_pkg / "ast_classes.py").write_text(_emit_ast_classes(model), encoding="utf-8")
    (src_pkg / "ast_dispatch.py").write_text(_emit_ast_dispatch(model), encoding="utf-8")
    (src_pkg / "provenance.py").write_text(
        _emit_provenance(model, opts, repo_root, generated_at), encoding="utf-8"
    )

    # Canonical normalize module copied into generated package verbatim (the
    # single normalizer implementation, issue #8).
    _norm_src = (Path(__file__).resolve().parents[1] / "normalize.py").read_text(encoding="utf-8")
    (src_pkg / "normalize.py").write_text(_norm_src, encoding="utf-8")

    # Package metadata + init re-exporting the generated classes
    (pkg_root / "pyproject.toml").write_text(
        env.get_template("pyproject.toml.j2").render(version=opts.version),
        encoding="utf-8",
    )
    (src_pkg / "__init__.py").write_text(
        env.get_template("pkg_init.py.j2").render(
            node_kinds=model.node_kinds(),
            discovered_keywords=sorted(set().union(*(d.keys() for d in model.dispatch_map().values())))
            if model.dispatch_map()
            else [],
        ),
        encoding="utf-8",
    )

    (pkg_root / "README.md").write_text(
        (
            "# sysml2py (generated)\n\n"
            "This package was generated by **sysml2py-lab** from the SysML v2\n"
            "grammar + relationship model + corpus (issues #6/#7/#8/#9).\n\n"
            "Capabilities:\n"
            "- Generated AST node classes per grammar rule\n"
            "- Membership-dispatch table from children.json\n"
            "- `dump()` / `get_definition()` on every node (loss-minimizing)\n"
            "- `Unsupported` nodes carry raw text (zero NotImplementedError)\n"
        ),
        encoding="utf-8",
    )

    _ruff_format(src_pkg)

    return pkg_root


def _ruff_format(src_pkg: Path) -> None:
    """Normalize generated code style via ruff (deterministic).

    The library ships a black.yml workflow, so generated code must pass
    black/ruff.  Rather than hand-formatting in every template, emit
    semantically-correct code then let ruff normalize style.  `ruff format`
    and `ruff check --fix` are deterministic, so this does not break the
    byte-identical determinism gate.  If ruff is unavailable, the generated
    tree is still emitted (style is a CI concern, not a correctness one).
    """
    import shutil
    import subprocess
    import sys
    from pathlib import Path as _P

    # Prefer the ruff that lives beside the running interpreter (the lab venv).
    ruff = shutil.which("ruff")
    if ruff is None:
        _candidate = _P(sys.executable).parent / "ruff"
        if _candidate.exists():
            ruff = str(_candidate)
    if ruff is None:
        return
    try:
        subprocess.run(
            [ruff, "check", "--fix", "--quiet", str(src_pkg)],
            check=False,
            capture_output=True,
        )
        # NOTE: normalize.py is a VERBATIM copy of the lab's single-sourced
        # canonical normalizer (issue #8) — it must stay byte-identical, so it
        # is excluded from ruff format (ruff format would rewrite its style).
        subprocess.run(
            [ruff, "format", "--quiet", str(src_pkg / "ast_classes.py")],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            [ruff, "format", "--quiet", str(src_pkg / "ast_dispatch.py")],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            [ruff, "format", "--quiet", str(src_pkg / "provenance.py")],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            [ruff, "format", "--quiet", str(src_pkg / "__init__.py")],
            check=False,
            capture_output=True,
        )
    except OSError as exc:  # pragma: no cover - defensive
        print(f"[emit] ruff post-format skipped: {exc}", file=sys.stderr)


_HEADER = """\
# Generated by sysml2py-lab — do not edit.
# Regenerate with: sysml2py-lab generate --out <dir>
"""
