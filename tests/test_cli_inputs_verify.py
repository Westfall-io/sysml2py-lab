from __future__ import annotations

from pathlib import Path

from sysml2py_lab.cli import main


def test_cli_inputs_verify_ok(tmp_path: Path, capsys):
    """CLI `sysml2py-lab inputs verify` succeeds on an intact grammar dir."""
    g = tmp_path / "g"
    g.mkdir()
    (g / "mini.xtext").write_text("grammar Mini\n", encoding="utf-8")

    rc = main(["inputs", "verify", "--dir", str(g)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inputs verify OK" in out
    assert "mini.xtext" in out


def test_cli_inputs_verify_drift(tmp_path: Path, capsys):
    """CLI reports failure (rc=1) when a vendored grammar file drifts."""
    g = tmp_path / "g"
    g.mkdir()
    f = g / "mini.xtext"
    f.write_text("grammar Mini\n", encoding="utf-8")
    assert main(["inputs", "verify", "--dir", str(g)]) == 0
    capsys.readouterr()  # flush

    # mutate
    f.write_text("grammar Mini\nRule: 'changed';\n", encoding="utf-8")
    rc = main(["inputs", "verify", "--dir", str(g)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "FAILED" in captured.err  # failure message goes to stderr
