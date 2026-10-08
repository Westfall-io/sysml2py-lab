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
import hashlib
import json
import re
import sys
from pathlib import Path

SOURCE_NAME = "SysML-v2-Release"
SOURCE_URL = "https://github.com/Systems-Modeling/SysML-v2-Release/tree/master/sysml/src"
LICENSE = "See SysML-v2-Release (Apache-2.0); original examples from OMG SysML v2"
WINDTRADER = "unverified"  # Windtrader adapter is issue #12, not yet wired

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
    """Extract commented-out text-assignment blocks (defined-test style).

    Heuristic: a commented `def test_X():` records the function name; a
    commented `# text = "` opens a block; the matching closing quote-run
    ends it.  Leading comment markers (`# `) on continuation lines are
    removed.  Model-string content (comments inside the examples) is
    preserved verbatim.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[tuple[str, str]] = []
    current_fn: str | None = None
    i = 0
    while i < len(lines):
        m = re.match(r"\s*#\s*def test_(\w+)\(\):", lines[i])
        if m:
            current_fn = m.group(1)
            i += 1
            continue
        m2 = re.match(r"\s*#\s*text\s*=\s*" + '"""', lines[i])
        if m2 and current_fn:
            buf: list[str] = []
            i += 1
            closed = False
            while i < len(lines):
                raw = lines[i]
                stripped = re.sub(r"^\s*#\s?", "", raw)
                buf.append(stripped)
                if '"""' in stripped:
                    closed = True
                    i += 1
                    break
                i += 1
            if closed:
                s = "".join(buf)
                s = s.replace('"""', "", 2) if s.count('"""') >= 2 else s
                out.append((f"{current_fn}__commented", s))
            current_fn = None
            continue
        i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True, help="path to sysml2py repo")
    ap.add_argument("--out", type=Path, required=True, help="corpus output dir")
    ap.add_argument("--include-commented", action="store_true")
    args = ap.parse_args()

    src = args.repo / "tests" / "grammar_test.py"
    if not src.exists():
        print(f"error: {src} not found", file=sys.stderr)
        return 1

    items = active_strings(src)
    if args.include_commented:
        items += commented_strings(src)

    out_dir = args.out / "grammar-2023-07"
    out_dir.mkdir(parents=True, exist_ok=True)

    entry = {}
    for fn, text in items:
        safe = fn
        fpath = out_dir / f"{safe}.sysml"
        fpath.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        entry[f"grammar-2023-07/{safe}.sysml"] = {
            "source": SOURCE_NAME,
            "source_url": SOURCE_URL,
            "license": LICENSE,
            "sha256": digest,
            "windtrader": WINDTRADER,
            "function": fn,
        }

    manifest = {
        "format": 1,
        "generated_by": "mine_corpus.py",
        "files": entry,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"mined {len(entry)} files -> {out_dir}")
    print(f"manifest -> {args.out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
