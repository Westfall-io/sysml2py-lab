"""Issue #12 — windtrader validation adapter tests.

Hermetic: these never invoke a JVM.  Adapter outcomes are driven through
Duck-typed result objects (mirroring windtrader-py's CommandResult) and
recorded fixtures, so the full exit-code mapping and the
adapter-vs-invalid distinction are locked without windtrader installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sysml2py_lab.validate import corpus as vcor
from sysml2py_lab.validate import windtrader as wt


# ---- fixtures / helpers -------------------------------------------------


class FakeResult:
    """Duck-typed stand-in for windtrader-py's CommandResult."""

    def __init__(self, exit_code: int = 0, stderr: str = ""):
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = ""
        self.duration_s = 0.0


def test_digest_is_stable_and_input_sensitive():
    d1 = wt.digest_text("part { attribute mass; }")
    d2 = wt.digest_text("part { attribute mass; }")
    d3 = wt.digest_text("part { attribute mass; x }")
    d4 = wt.digest_text("part { attribute mass; }", version="0.1.0")
    assert d1 == d2
    assert d1 != d3
    assert d1 != d4
    assert len(d1) == 64  # sha256 hex


# ---- exit-code → status mapping ----------------------------------------


@pytest.mark.parametrize(
    ("exit_code", "expected_status"),
    [
        (0, "valid"),
        (2, "invalid"),
        (3, "adapter_error"),   # runtime/tool failure
        (1, "adapter_error"),   # unexpected
        (4, "adapter_error"),
    ],
)
def test_exit_code_to_status(exit_code, expected_status):
    v = wt.normalize_result(FakeResult(exit_code=exit_code))
    assert v.status == expected_status


def test_valid_verdict_ok_true():
    v = wt.normalize_result(FakeResult(exit_code=0))
    assert v.ok is True
    assert v.exit_code == 0
    assert v.recorded is False


def test_invalid_and_error_not_ok():
    assert wt.normalize_result(FakeResult(exit_code=2)).ok is False
    assert wt.normalize_result(FakeResult(exit_code=3)).ok is False


def test_diagnostics_normalized():
    v = wt.normalize_result(FakeResult(exit_code=2, stderr="error: line=1 offset=14 near=mass\n\n"))
    assert v.diagnostics == ["error: line=1 offset=14 near=mass"]


# ---- the critical distinction: adapter_error vs invalid ----------------


def test_runtime_error_is_adapter_error_not_invalid():
    """exit 3 (tool crash) must NOT be conflated with 'the model is bad'."""
    v = wt.normalize_result(FakeResult(exit_code=3, stderr="java.lang.OOM"))
    assert v.status == "adapter_error"
    assert v.ok is False


def test_missing_fixture_is_adapter_error():
    """A missing fixture on a hermetic run is a tooling failure, never a
    silent valid or invalid verdict."""
    with pytest.raises(wt.AdapterError):
        wt.validate_text("nope", recorded={})


# ---- recorded-fixture replay -------------------------------------------


def test_offline_replay_valid():
    text = "part { attribute mass; }"
    dig = wt.digest_text(text)
    fx = {dig: {"status": "valid", "exit_code": 0, "version": "0.2.0",
                "diagnostics": []}}
    v = wt.validate_text(text, recorded=fx)
    assert v.status == "valid"
    assert v.ok is True
    assert v.recorded is True


def test_offline_replay_invalid():
    text = "part { bad nope }"
    dig = wt.digest_text(text)
    fx = {dig: {"status": "invalid", "exit_code": 2, "version": "0.2.0",
                "diagnostics": ["error: line=1 near=nope"]}}
    v = wt.validate_text(text, recorded=fx)
    assert v.status == "invalid"
    assert v.recorded is True


def test_fixtures_roundtrip_save_load(tmp_path):
    text = "part { attribute mass; }"
    dig = wt.digest_text(text)
    fx = {dig: {"status": "valid", "exit_code": 0, "version": "0.2.0",
                "diagnostics": []}}
    p = tmp_path / "fx.json"
    wt.save_fixtures(p, fx)
    loaded = wt.load_fixtures(p)
    assert loaded == fx
    assert wt.load_fixtures(tmp_path / "missing.json") == {}


# ---- corpus orchestration ----------------------------------------------


def _make_corpus(tmp_path, files: dict[str, str]) -> Path:
    """Build a tiny corpus dir with a manifest; returns the dir path."""
    entries = {}
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        entries[rel] = {"windtrader": "unverified"}
    (tmp_path / "manifest.json").write_text(
        json.dumps({"files": entries, "format": 1}, sort_keys=True),
        encoding="utf-8",
    )
    return tmp_path


def test_compute_verdicts_inputs_only(monkeypatch, tmp_path):
    cd = _make_corpus(tmp_path, {
        "a.sysml": "part { attribute mass; }",   # -> valid
        "b.sysml": "part { bad nope }",          # -> invalid
    })
    # record true verdicts by running through the adapter path with fixtures
    fx = {
        wt.digest_text("part { attribute mass; }"): {"status": "valid", "exit_code": 0,
                                                      "version": "0.2.0", "diagnostics": []},
        wt.digest_text("part { bad nope }"): {"status": "invalid", "exit_code": 2,
                                               "version": "0.2.0",
                                               "diagnostics": ["error: line=1 near=nope"]},
    }
    verdicts = vcor.compute_verdicts(cd, recorded=fx)
    assert verdicts["a.sysml"]["input_status"] == "valid"
    assert verdicts["b.sysml"]["input_status"] == "invalid"
    s = vcor.summarize(verdicts)
    assert s["input_valid"] == 1
    assert s["input_invalid"] == 1
    assert s["input_adapter_errors"] == 0
    assert s["roundtrip_present"] == 0  # no pkg supplied
    assert s["files"] == 2


def test_active_adapter_error_distinct_in_summary(monkeypatch, tmp_path):
    """Without fixtures, an unimportable windtrader must be an adapter
    error, not an invalid — the gate can tell 'tool broke' from 'model bad'."""
    import sysml2py_lab.validate.windtrader as wtmod
    # monkeypatch _import_validate to raise AdapterError
    def _boom():
        raise wt.AdapterError("windtrader is not importable: boom")
    monkeypatch.setattr(wtmod, "_import_validate", _boom)
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute mass; }"})
    verdicts = vcor.compute_verdicts(cd)  # no recorded -> live path -> AdapterError
    assert verdicts["a.sysml"]["input_status"] == "adapter_error"
    s = vcor.summarize(verdicts)
    assert s["input_adapter_errors"] == 1
    assert s["input_invalid"] == 0


def test_update_manifest_records_verdict(tmp_path):
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute mass; }"})
    fx = {wt.digest_text("part { attribute mass; }"): {"status": "valid", "exit_code": 0,
                                                        "version": "0.2.0", "diagnostics": []}}
    verdicts = vcor.compute_verdicts(cd, recorded=fx)
    valid, invalid = vcor.update_manifest_verdicts(cd, verdicts)
    assert (valid, invalid) == (1, 0)
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    field = m["files"]["a.sysml"]["windtrader"]
    assert field["status"] == "valid"
    assert field["version"] == "0.2.0"
    assert field["exit_code"] == 0


def test_roundtrip_verdict_recorded_in_manifest(monkeypatch, tmp_path):
    """AC#2: the generator-output (round-trip) verdict is validated and
    recorded in the manifest, distinct from the input verdict.

    Uses a fake generated package: we patch _roundtrip_outputs to return a
    specific emitted text, then assert the manifest records its verdict.
    """
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute mass; }"})
    emitted = "part { attribute mass; }"  # what the generator emits
    fx = {
        wt.digest_text("part { attribute mass; }"): {"status": "valid", "exit_code": 0,
                                                      "version": "0.2.0", "diagnostics": []},
        wt.digest_text(emitted): {"status": "valid", "exit_code": 0,
                                   "version": "0.2.0", "diagnostics": []},
    }
    import sysml2py_lab.validate.corpus as vc
    monkeypatch.setattr(vc, "_roundtrip_outputs",
                        lambda cd_, pkg: ({"a.sysml": emitted}, {}))
    verdicts = vcor.compute_verdicts(cd, generated_pkg=tmp_path / "pkg", recorded=fx)
    assert verdicts["a.sysml"]["roundtrip_status"] == "valid"
    assert verdicts["a.sysml"]["roundtrip_exit"] == 0
    vcor.update_manifest_verdicts(cd, verdicts)
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    field = m["files"]["a.sysml"]["windtrader"]
    assert field["roundtrip_status"] == "valid"
    assert field["roundtrip_exit"] == 0


def test_roundtrip_invalid_propagates_to_manifest(monkeypatch, tmp_path):
    """AC#2: if the generator emits text windtrader rejects, the manifest
    round-trip verdict records it — never masked as valid."""
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute mass; }"})
    emitted = "part { attribute mass; ;; }"   # generator emits something bad
    fx = {
        wt.digest_text("part { attribute mass; }"): {"status": "valid", "exit_code": 0,
                                                      "version": "0.2.0", "diagnostics": []},
        wt.digest_text(emitted): {"status": "invalid", "exit_code": 2,
                                   "version": "0.2.0",
                                   "diagnostics": ["error: line=1 near=;;"]},
    }
    import sysml2py_lab.validate.corpus as vc
    monkeypatch.setattr(vc, "_roundtrip_outputs",
                        lambda cd_, pkg: ({"a.sysml": emitted}, {}))
    verdicts = vcor.compute_verdicts(cd, generated_pkg=tmp_path / "pkg", recorded=fx)
    assert verdicts["a.sysml"]["roundtrip_status"] == "invalid"
    s = vcor.summarize(verdicts)
    assert s["roundtrip_invalid"] == 1  # emitted-output invalidity (B1 axis)
    assert s["roundtrip_present"] == 1


# ---- ratchet (issue-#12 protocol §6) ------------------------------------


def _corpus_with_baseline(tmp_path, baseline: dict[str, str], current: dict[str, str]) -> Path:
    """Build a corpus whose manifest already records `baseline` verdicts and
    whose files' `current` texts are what we re-validate against."""
    entries = {}
    for rel, text in current.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        entries[rel] = {"windtrader": {"status": baseline.get(rel, "unverified"),
                                        "version": "0.2.0", "exit_code": 0}}
    (tmp_path / "manifest.json").write_text(
        json.dumps({"files": entries, "format": 1}, sort_keys=True), encoding="utf-8")
    return tmp_path


def _fixtures_for(files: dict[str, str]) -> dict[str, dict]:
    return {wt.digest_text(t, "0.2.0"): {"status": "valid", "exit_code": 0,
                                          "version": "0.2.0", "diagnostics": []}
            for t in files.values()}


def test_ratchet_no_regression_when_baseline_invalid(tmp_path):
    """A file whose BASELINE is invalid that stays invalid is NOT a
    regression — tracked WIP, not a gate failure."""
    cd = _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "invalid"},
                               current={"a.sysml": "part { still bad }"})
    # current text for a.sysml is validated invalid via fixture
    fx = {wt.digest_text("part { still bad }", "0.2.0"):
          {"status": "invalid", "exit_code": 2, "version": "0.2.0",
           "diagnostics": ["error: near=bad"]}}
    verdicts = vcor.compute_verdicts(cd, recorded=fx)
    regressions = vcor.ratchet_regressions(cd, verdicts)
    assert regressions == []


def test_ratchet_blocks_valid_to_invalid(tmp_path):
    """A file that WAS valid but now validates invalid is a regression."""
    cd = _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "valid"},
                               current={"a.sysml": "part { now bad }"})
    fx = {wt.digest_text("part { now bad }", "0.2.0"):
          {"status": "invalid", "exit_code": 2, "version": "0.2.0",
           "diagnostics": ["error: near=bad"]}}
    verdicts = vcor.compute_verdicts(cd, recorded=fx)
    regressions = vcor.ratchet_regressions(cd, verdicts)
    assert any("a.sysml" in r and "valid ->" in r for r in regressions)


def test_ratchet_blanks_adapter_error_regression(monkeypatch, tmp_path):
    """A file that WAS valid but now tooling breaks (adapter_error) is ALSO
    a regression — the gate must catch tooling degradation."""
    cd = _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "valid"},
                               current={"a.sysml": "part { attribute x; }"})
    # no fixture -> live path -> we force _import_validate to break
    import sysml2py_lab.validate.windtrader as wtmod
    def _boom():
        raise wt.AdapterError("java missing")
    monkeypatch.setattr(wtmod, "_import_validate", _boom)
    verdicts = vcor.compute_verdicts(cd)
    regressions = vcor.ratchet_regressions(cd, verdicts)
    assert any("valid -> adapter_error" in r for r in regressions)


def test_new_invalid_file_flagged_unbaselined(tmp_path):
    """AC#3 (B2): a NEW file with no baseline that is invalid must be
    flagged by unbaselined_files (the ratchet itself is not the AC#3
    mechanism — unbaselined_files is, and the CLI gate consumes it)."""
    cd = _corpus_with_baseline(tmp_path,
                               baseline={},
                               current={"new.sysml": "part { bad new }"})
    fx = {wt.digest_text("part { bad new }"): {"status": "invalid", "exit_code": 2,
                                               "version": "0.2.0",
                                               "diagnostics": ["error: near=bad"]}}
    verdicts = vcor.compute_verdicts(cd, recorded=fx)
    # no valid->invalid regression (there was no baseline), but unbaselined_files
    # must flag it as AC#3 gate material.
    assert vcor.ratchet_regressions(cd, verdicts) == []
    flagged = vcor.unbaselined_files(cd, verdicts)
    assert any("new.sysml" in f for f in flagged)


# ---- CLI gate exit codes (r1 B4: the merge gate itself must be tested) ---


def test_record_verdicts_hard_fails_on_adapter_error(monkeypatch, tmp_path):
    """W1-8: record_verdicts must raise on adapter_error (recording never
    bakes in a failure)."""
    import sysml2py_lab.validate.windtrader as wtmod

    class _CmdResult:
        exit_code = 3  # adapter failure
        stdout = ""
        stderr = "java: not found"
    monkeypatch.setattr(wtmod, "_import_validate", lambda: (lambda t, version="0.2.0": _CmdResult()))
    with pytest.raises(wt.AdapterError):
        wt.record_verdicts({"x.sysml": "part {}"}, version="0.2.0")


def test_record_verdicts_records_roundtrip_extra(monkeypatch, tmp_path):
    """W1-8: record_verdicts records the round-trip digest via extra_texts
    with provenance 'file', keyed under the same name."""
    import sysml2py_lab.validate.windtrader as wtmod

    calls = []
    def _fake_validate(text, version="0.2.0"):
        calls.append(text)
        return wt.Verdict(status="valid", exit_code=0, version=version,
                           diagnostics=[])
    monkeypatch.setattr(wtmod, "_import_validate", lambda: _fake_validate)
    fx, verdicts = wt.record_verdicts(
        {"a.sysml": "part { attribute x; }"}, version="0.2.0",
        extra_texts={"a.sysml": "part { emitted }"},
        provenance={"a.sysml": "sub/a.sysml"},
    )
    assert len(fx) == 2  # input + roundtrip digests
    for entry in fx.values():
        assert entry["file"] == "sub/a.sysml"
    assert len(calls) == 2


def test_single_file_valid_exits_zero(monkeypatch, tmp_path):
    """r3 W1-A: a valid single file exits 0 (the r2 blocker was this
    specific path returning 1 via KeyError)."""
    f = tmp_path / "good.sysml"
    f.write_text("part { attribute mass; }")
    fx = {wt.digest_text("part { attribute mass; }"): {"status": "valid", "exit_code": 0,
                                                       "version": "0.2.0", "diagnostics": []}}
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(f), "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 0


def test_single_file_invalid_exits_one(monkeypatch, tmp_path):
    """r3 W1-A: an invalid single file exits 1 with a GATE message."""
    f = tmp_path / "bad.sysml"
    f.write_text("part { bad syntax ; }")
    fx = {wt.digest_text("part { bad syntax ; }"): {"status": "invalid", "exit_code": 2,
                                                     "version": "0.2.0",
                                                     "diagnostics": ["error: line=1 near=bad"]}}
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(f), "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 1


def test_build_fixture_dict_refuses_adapter_error(tmp_path):
    """r3 W1-B: the trust-anchor builder refuses to bake broken verdicts."""
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute x; }"})
    verdicts = {"a.sysml": {"input_status": "adapter_error", "input_exit": None,
                            "roundtrip_status": None, "diagnostics": []}}
    with pytest.raises(wt.AdapterError):
        vcor.build_fixture_dict(cd, verdicts, version="0.2.0")


def test_build_fixture_dict_emits_roundtrip_digest_with_rt_diagnostics(tmp_path):
    """r3 W1-B: round-trip digests are emitted and carry their OWN
    diagnostics (never the input's)."""
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute x; }"})
    verdicts = {
        "a.sysml": {
            "input_status": "valid", "input_exit": 0,
            "roundtrip_status": "invalid", "roundtrip_exit": 2,
            "diagnostics": ["input diag"],
            "roundtrip_diagnostics": ["rt diag"],
        }
    }
    fx = vcor.build_fixture_dict(cd, verdicts, version="0.2.0",
                                 rt_texts={"a.sysml": "part { emitted }"})
    assert len(fx) == 2  # input digest + round-trip digest
    rt_dig = wt.digest_text("part { emitted }", "0.2.0")
    assert fx[rt_dig]["status"] == "invalid"
    assert fx[rt_dig]["diagnostics"] == ["rt diag"]  # NOT input diag


def test_emit_error_files_and_summarize(tmp_path):
    """r3 W1-B: emit_error_files surface generator crashes distinctly."""
    verdicts = {
        "a.sysml": {"input_status": "valid", "roundtrip_status": "emit_error",
                    "emit_error": "ValueError: boom", "input_exit": 0}
    }
    assert vcor.emit_error_files(verdicts) == ["a.sysml: ValueError: boom"]
    s = vcor.summarize(verdicts)
    assert s["roundtrip_emit_errors"] == 1
    assert s["roundtrip_present"] == 1


def test_corpus_add_refuses_invalid_without_allow(tmp_path, monkeypatch):
    """r4 W1-2: corpus add refuses an invalid file (AC#3) by default."""
    cd = _make_corpus(tmp_path, {})  # corpus dir + empty manifest
    newf = tmp_path / "new.sysml"
    newf.write_text("part { bad syntax ; }")
    fx = {wt.digest_text("part { bad syntax ; }"): {"status": "invalid", "exit_code": 2,
                                                     "version": "0.2.0", "diagnostics": ["err"]}}
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    monkeypatch.setattr(wt, "_import_validate", lambda: (lambda t, version="0.2.0": wt.Verdict(
        status="invalid", exit_code=2, version="0.2.0", diagnostics=["err"])))
    from sysml2py_lab import cli as cli_mod
    # must pass --offline? no: use the monkeypatched live validator
    rc = cli_mod.main(["corpus", "add", str(newf), "--corpus", str(cd)])
    assert rc == 1
    m = json.loads(open(cd / "manifest.json").read())
    assert "new.sysml" not in m["files"]  # not added


def test_corpus_add_allow_invalid_stamps_manifest(tmp_path, monkeypatch):
    """r4 W1-2/W1-D: --allow-invalid adds the file AND stamps the manifest
    with status invalid (baselines as tracked WIP, not unverified)."""
    cd = _make_corpus(tmp_path, {})
    newf = tmp_path / "new.sysml"
    newf.write_text("part { bad syntax ; }")
    monkeypatch.setattr(wt, "_import_validate", lambda: (lambda t, version="0.2.0": wt.Verdict(
        status="invalid", exit_code=2, version="0.2.0", diagnostics=["err"])))
    from sysml2py_lab import cli as cli_mod
    rc = cli_mod.main(["corpus", "add", "--allow-invalid",
                       str(newf), "--corpus", str(cd)])
    assert rc == 0
    m = json.loads(open(cd / "manifest.json").read())
    assert "local/new.sysml" in m["files"]
    wt_field = m["files"]["local/new.sysml"]["windtrader"]
    assert wt_field["status"] == "invalid"


def test_manifest_writer_refuses_adapter_error(tmp_path):
    """r4 W2-3: update_manifest_verdicts refuses to bake adapter_error."""
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute mass; }"})
    verdicts = {"a.sysml": {"input_status": "adapter_error", "input_exit": None}}
    with pytest.raises(wt.AdapterError):
        vcor.update_manifest_verdicts(cd, verdicts)


def test_compute_verdicts_gates_untracked_file(tmp_path):
    """r4 W2-6: a .sysml on disk with no manifest entry raises."""
    cd = _make_corpus(tmp_path, {"a.sysml": "part { attribute mass; }"})
    (cd / "rogue.sysml").write_text("part { attribute mass; }")
    with pytest.raises(vcor.UntrackedFilesError):
        vcor.compute_verdicts(cd, recorded={})


def test_validate_flag_combos_rejected(tmp_path, monkeypatch):
    """r4 W2-1/W2-2: incoherent flag combos exit 2 before any work."""
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", "corpus", "--record", "fx.json",
                           "--offline", "other.json"])
    finally:
        monkeypatch.chdir("/")
    assert rc == 2
    # --record without --generated
    monkeypatch.chdir(tmp_path)
    try:
        rc2 = cli_mod.main(["validate", "corpus", "--record", "fx.json"])
    finally:
        monkeypatch.chdir("/")
    assert rc2 == 2


def test_gate_offline_generated_replays_roundtrip(monkeypatch, tmp_path):
    """B6 coverage (r2 W1-4): `--generated --offline` replays the round-trip
    digests — this is the exact scenario the CLI now supports, and it was
    never exercised before."""
    _corpus_with_baseline(tmp_path,
                          baseline={"a.sysml": "valid"},
                          current={"a.sysml": "part { attribute x; }"})
    emitted = "part { also fine }"  # generator emits valid SysML
    fx = {
        wt.digest_text("part { attribute x; }"): {"status": "valid", "exit_code": 0,
                                                  "version": "0.2.0", "diagnostics": []},
        wt.digest_text(emitted): {"status": "valid", "exit_code": 0,
                                  "version": "0.2.0", "diagnostics": []},
    }
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    import sysml2py_lab.validate.corpus as vc
    monkeypatch.setattr(vc, "_roundtrip_outputs",
                        lambda cd_, pkg: ({"a.sysml": emitted}, {}))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(tmp_path),
                           "--generated", str(tmp_path / "pkg"),
                           "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 0  # input AND round-trip both replay from fixtures, exit 0


def test_gate_passes_on_baseline_invalids(monkeypatch, tmp_path):
    """A corpus with tracked baseline invalids passes the ratchet gate."""
    _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "valid", "b.sysml": "invalid"},
                               current={"a.sysml": "part { attribute x; }",
                                        "b.sysml": "part { still bad }"})
    fx = {
        wt.digest_text("part { attribute x; }"): {"status": "valid", "exit_code": 0,
                                                  "version": "0.2.0", "diagnostics": []},
        wt.digest_text("part { still bad }"): {"status": "invalid", "exit_code": 2,
                                               "version": "0.2.0",
                                               "diagnostics": ["error: near=bad"]},
    }
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(tmp_path), "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 0


def test_gate_fails_on_valid_to_invalid_regression(monkeypatch, tmp_path):
    """A valid-baseline file going invalid FAILS the gate (exit 1)."""
    _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "valid"},
                               current={"a.sysml": "part { now bad }"})
    fx = {wt.digest_text("part { now bad }"): {"status": "invalid", "exit_code": 2,
                                               "version": "0.2.0",
                                               "diagnostics": ["error: near=bad"]}}
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(tmp_path), "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 1


def test_gate_fails_on_adapter_error(monkeypatch, tmp_path):
    """Adapter errors FAIL the gate (exit 1), even with baseline invalids."""
    _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "valid"},
                               current={"a.sysml": "part { attribute x; }"})
    fx = {}  # empty fixtures -> missing fixture -> AdapterError on every file
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(tmp_path), "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 1


def test_gate_fails_on_new_unbaselined_invalid_file(monkeypatch, tmp_path):
    """A NEW file that is invalid FAILS (AC#3, B2)."""
    # manifest has NO baseline status entry for the on-disk file: the helper
    # stamps every present file as "unverified", which unbaselined_files
    # treats as no-valid-baseline — the AC#3 branch.
    _corpus_with_baseline(tmp_path,
                               baseline={"other.sysml": "valid"},
                               current={"other.sysml": "part { attribute o; }",
                                        "new.sysml": "part { bad new }"})
    fx = {
        wt.digest_text("part { attribute o; }"): {"status": "valid", "exit_code": 0,
                                                  "version": "0.2.0", "diagnostics": []},
        wt.digest_text("part { bad new }"): {"status": "invalid", "exit_code": 2,
                                             "version": "0.2.0",
                                             "diagnostics": ["error: near=bad"]},
    }
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(tmp_path), "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 1


def test_gate_fails_when_roundtrip_invalid(monkeypatch, tmp_path):
    """B1: input valid but generator round-trip output invalid FAILS."""
    _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "valid"},
                               current={"a.sysml": "part { attribute x; }"})
    emitted = "part { bad emitted ; }"  # generator emits invalid SysML
    fx = {
        wt.digest_text("part { attribute x; }"): {"status": "valid", "exit_code": 0,
                                                  "version": "0.2.0", "diagnostics": []},
        wt.digest_text(emitted): {"status": "invalid", "exit_code": 2,
                                  "version": "0.2.0",
                                  "diagnostics": ["error: near=bad"]},
    }
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    import sysml2py_lab.validate.corpus as vc
    monkeypatch.setattr(vc, "_roundtrip_outputs",
                        lambda cd_, pkg: ({"a.sysml": emitted}, {}))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(tmp_path),
                           "--generated", str(tmp_path / "pkg"),
                           "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 1


def test_gate_fails_on_emit_error(monkeypatch, tmp_path):
    """B5: a generator crash (emit_error) FAILS, distinct from windtrader."""
    _corpus_with_baseline(tmp_path,
                               baseline={"a.sysml": "valid"},
                               current={"a.sysml": "part { attribute x; }"})
    fx = {wt.digest_text("part { attribute x; }"): {"status": "valid", "exit_code": 0,
                                                    "version": "0.2.0", "diagnostics": []}}
    fp = tmp_path / "fx.json"
    json.dump(fx, open(fp, "w"))
    import sysml2py_lab.validate.corpus as vc
    monkeypatch.setattr(vc, "_roundtrip_outputs",
                        lambda cd_, pkg: ({}, {"a.sysml": "ValueError: boom"}))
    from sysml2py_lab import cli as cli_mod
    monkeypatch.chdir(tmp_path)
    try:
        rc = cli_mod.main(["validate", str(tmp_path),
                           "--generated", str(tmp_path / "pkg"),
                           "--offline", str(fp)])
    finally:
        monkeypatch.chdir("/")
    assert rc == 1
