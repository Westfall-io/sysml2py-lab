from __future__ import annotations

from pathlib import Path

from sysml2py_lab.cli import main


def test_cli_inputs_record_then_verify(tmp_path: Path, capsys):
    """`inputs record` then `inputs verify` succeed on an intact grammar dir."""
    g = tmp_path / "g"
    g.mkdir()
    (g / "mini.xtext").write_text("grammar Mini\n", encoding="utf-8")

    rc = main(["inputs", "record", "--dir", str(g)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inputs record OK" in out
    assert "mini.xtext" in out

    rc = main(["inputs", "verify", "--dir", str(g)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inputs verify OK" in out


def test_cli_inputs_verify_drift(tmp_path: Path, capsys):
    """CLI reports failure (rc=1) when a vendored grammar file drifts."""
    g = tmp_path / "g"
    g.mkdir()
    f = g / "mini.xtext"
    f.write_text("grammar Mini\n", encoding="utf-8")
    assert main(["inputs", "record", "--dir", str(g)]) == 0
    capsys.readouterr()  # flush

    # mutate
    f.write_text("grammar Mini\nRule: 'changed';\n", encoding="utf-8")
    rc = main(["inputs", "verify", "--dir", str(g)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "FAILED" in captured.err  # failure message goes to stderr


def test_cli_spec_writes_json(tmp_path: Path, capsys):
    """CLI `sysml2py-lab spec` builds language_spec.json from a grammar dir."""
    g = tmp_path / "g"
    g.mkdir()
    (g / "mini.xtext").write_text(
        "grammar Mini\nRuleDef returns X::Y : foo = Bar;\n", encoding="utf-8"
    )
    out = tmp_path / "out" / "language_spec.json"
    rc = main(["spec", "--dir", str(g), "--out", str(out)])
    captured = capsys.readouterr()
    assert rc == 0
    assert out.exists()
    assert "spec wrote" in captured.out
    assert "rules=1" in captured.out
