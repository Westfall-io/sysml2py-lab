#!/usr/bin/env python3
"""Mine inline SysML model strings from sysml2py/tests/grammar_test.py
into the corpus (issue #8).

The grammar_test.py file holds 60 inline model strings copied from
SysML-v2-Release sysml/src, plus 8 commented-out ones.  This script:

  - parses grammar_test.py as Python (AST) to extract the ACTIVE
    text-assignment strings, named by their enclosing test function;
  - also extracts the COMMENTED-OUT strings (opt-in, --include-commented),
    which are not executed but are valid SysML examples;
  - writes each as corpus/grammar-2023-07/<function>.sysml;
  - preserves exact bytes and records provenance in the manifest.

Usage:
  mine_corpus.py --repo /path/to/sysml2py --out corpus [--include-commented]
"""

import argparse
import ast
import re
import sys
from pathlib import Path

SOURCE_NAME = "SysML-v2-Release"
SOURCE_URL = "https://github.com/Systems-Modeling/SysML-v2-Release/tree/master/sysml/src"
LICENSE = "See SysML-v2-Release (Apache-2.0); original examples from OMG SysML v2"

# NOTE: no literal triple-quote sequences appear in any docstring below to
# avoid breaking Python parsing.


def active_strings(path: Path) -> list[tuple[str, str]]:
    """Extract (test_function_name, text) for active text assignments."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "text"
                        for t in stmt.targets
                    )
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    out.append((node.name, stmt.value.value))
    return out


def commented_strings(path: Path) -> list[tuple[str, str]]:
    """Extract commented-out text-assignment blocks faithfully.

    The source comments out whole tests like::

        # def test_package():
        #     text = "package Package1;"
        ...
        # def test_subpackage():
        #     text = \"\"\"
        #     package Package1 {
        #         package Package2;
        #     }\"\"\"

    This handles BOTH the single-quoted one-liner form and the triple-quoted
    form (with same-line or multiline close), stripping the leading comment
    marker from each line so the captured string is exactly what `text`
    would have held.  Returns (function_name, model_string) pairs, each
    named `<fn>__commented`.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[tuple[str, str]] = []
    i = 0
    current_fn: str | None = None
    while i < len(lines):
        line = lines[i]
        m_fn = re.match(r"\s*#\s*def test_(\w+)\(\):\s*$", line)
        if m_fn:
            current_fn = m_fn.group(1)
            i += 1
            continue
        m = re.match(r'\s*#\s*text\s*=\s*("""|")', line)
        if m and current_fn:
            delim = m.group(1)
            rest = line[m.end():]  # content on the opener line after the quote
            buf: list[str] = []
            if delim == '"':
                # single-quoted: everything to the closing quote on this line
                if '"' in rest:
                    body = rest.split('"', 1)[0]
                    if body:
                        buf.append(body)
            else:  # '"""'
                close_re = r'"""'
                if close_re in rest:
                    # same-line close: content before the closing '"""'
                    body = rest.split('"""', 1)[0]
                    if body:
                        buf.append(body.rstrip("\n"))
                else:
                    # opener line may carry content (e.g. `# text = """package Package1 {`)
                    if rest.strip():
                        buf.append(rest.rstrip("\n"))
                    # scan forward for the closing '"""'
                    i += 1
                    closed = False
                    while i < len(lines):
                        raw = lines[i]
                        stripped = re.sub(r"^\s*#\s?", "", raw).rstrip("\n")
                        if '"""' in stripped:
                            before = stripped.split('"""', 1)[0]
                            if before:
                                buf.append(before.rstrip("\n"))
                            closed = True
                            i += 1
                            break
                        buf.append(stripped)
                        i += 1
                    if not closed:
                        continue
            out.append((f"{current_fn}__commented", "\n".join(buf).strip()))
            current_fn = None
        i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True, help="path to sysml2py repo")
    ap.add_argument("--out", type=Path, required=True, help="corpus output dir")
    ap.add_argument("--include-commented", action="store_true")
    ap.add_argument("--model", type=Path, default=None, help="children.json path")
    args = ap.parse_args()

    src = args.repo / "tests" / "grammar_test.py"
    if not src.exists():
        print(f"error: {src} not found", file=sys.stderr)
        return 1

    items = active_strings(src)
    if args.include_commented:
        items += commented_strings(src)

    # Write the mined .sysml files, then route EVERY entry through
    # corpus.add_file so the manifest is merged (never clobbered) and each
    # entry carries fidelity + node_kinds.  This keeps `corpus verify` green
    # after mining (C3) and guarantees a reproducible manifest.
    from sysml2py_lab.corpus import add_file

    out_dir = args.out / "grammar-2023-07"
    out_dir.mkdir(parents=True, exist_ok=True)

    for fn, text in items:
        fpath = out_dir / f"{fn}.sysml"
        # write only if the bytes changed (idempotent, deterministic)
        if not fpath.exists() or fpath.read_text(encoding="utf-8") != text:
            fpath.write_text(text, encoding="utf-8")
        rel = add_file(
            args.out,
            fpath,
            source=SOURCE_NAME,
            source_url=SOURCE_URL,
            license=LICENSE,
            dest_subdir="grammar-2023-07",
            model_path=str(args.model) if args.model else None,
        )
        assert rel == f"grammar-2023-07/{fn}.sysml"

    print(f"mined {len(items)} files -> {out_dir}")
    print(f"manifest -> {args.out / 'manifest.json'} (merged, {len(items)} grammar entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
