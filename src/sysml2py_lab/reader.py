"""read_model: load a .sysml file, edit it, and emit lossless SysML.

This is the read side of the read-modify-write loop.  A user script does::

    from sysml2py_lab import read_model
    from sysml2py import PackageBuilder

    model = read_model("file.sysml")        # -> an editable model (ReadModel)
    model.add(PackageBuilder("C"))          # edit with the normal builder API
    open("out.sysml", "w").write(model.dump())

The parser lives HERE (the lab), per the agreed design (B): the distributable
``sysml2py`` stays builder-only.

Fidelity (cast-true): ``dump()`` is LOSSLESS.  The model wraps the parsed IR
tree, and ``dump()`` renders it via ``render_ir`` — which is canonical-equal
on 61/61 corpus files (100%).  Edits via ``model.add(builder)`` are appended;
the builder renders faithfully for authored content, so an injected package
survives.  Untouched source content is preserved — nothing is silently
dropped, reordered, or invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ir import IRNode, ir_to_json, parse_ir, render_ir


# ---------------------------------------------------------------------------
# The editable, lossless model
# ---------------------------------------------------------------------------


class ReadModel:
    """Editable wrapper over the lossless IR tree of a parsed .sysml file.

    ``dump()`` renders the IR tree losslessly via ``render_ir`` (canonical-
    equal on the whole corpus).  ``add()`` appends a builder-authored element
    to the root namespace, splicing its rendered text into the output.

    Children of the root (the top-level namespace) are reachable so a script
    can navigate/insert into existing packages, but the lossless guarantee
    applies to whatever is untouched: only what a script explicitly ``add``s
    is rendered fresh; the rest re-emits the original raw text.
    """

    def __init__(self, root: IRNode):
        self._root = root
        self._added: list[IRNode] = []

    @property
    def children(self):
        """Live list of the root namespace's members (for navigation)."""
        return self._root.children

    def add(self, builder_or_raw) -> "ReadModel":
        """Append an element to the top-level namespace.

        Accepts a ``sysml2py`` Builder (rendered faithfully via its
        ``dump()``) or a string of raw SysML text.  Returns self for chaining.
        """
        if isinstance(builder_or_raw, str):
            text = builder_or_raw
        elif hasattr(builder_or_raw, "dump"):
            text = builder_or_raw.dump()
        else:
            raise TypeError(
                "add() expects a sysml2py Builder or a raw text string, "
                f"got {type(builder_or_raw).__name__}"
            )
        node = IRNode(
            kind="added",
            name="",
            raw_text=text,
            fidelity="modelled",
        )
        self._added.append(node)
        return self

    def dump(self) -> str:
        """Emit the model losslessly as SysML text."""
        return render_ir(self._root) + self._render_added()

    def _render_added(self) -> str:
        if not self._added:
            return ""
        text = "".join(n.raw_text for n in self._added)
        return text + ("\n" if not text.endswith("\n") else "")


# ---------------------------------------------------------------------------
# read_model entry point
# ---------------------------------------------------------------------------


def read_model(
    file: str | Path,
    *,
    model_path: str | None = None,
) -> ReadModel:
    """Load a ``.sysml`` text file into an editable, lossless model.

    Args:
        file: Path to the ``.sysml`` file to read.
        model_path: Optional path to the issue-6 ``children.json`` used to
            classify kind/fidelity (default: repo spec relationships model).

    Returns:
        A ``ReadModel`` whose ``dump()`` emits the lossless SysML text and
        whose ``add()`` appends builder-authored elements.
    """
    src_str = Path(file).read_text(encoding="utf-8")
    root = parse_ir(src_str, model_path=model_path)
    return ReadModel(root)
