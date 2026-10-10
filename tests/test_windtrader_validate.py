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
    assert s["invalid"] == 1
    assert s["adapter_errors"] == 0
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
    assert s["adapter_errors"] == 1
    assert s["invalid"] == 0


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
    monkeypatch.setattr(vc, "_roundtrip_outputs", lambda cd_, pkg: {"a.sysml": emitted})
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
    monkeypatch.setattr(vc, "_roundtrip_outputs", lambda cd_, pkg: {"a.sysml": emitted})
    verdicts = vcor.compute_verdicts(cd, generated_pkg=tmp_path / "pkg", recorded=fx)
    assert verdicts["a.sysml"]["roundtrip_status"] == "invalid"
    s = vcor.summarize(verdicts)
    assert s["invalid"] == 1  # the emitted-output invalidity gates
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


def test_ratchet_ignores_unverified_nonunion(tmp_path):
    """A file never in the corpus (no baseline) is handled: if it appears in
    verdicts (new), the AC#3 intent is that it must be valid. Here with a
    missing manifest baseline it's not a valid->invalid regression, so no
    ratchet regression is reported."""
    cd = _corpus_with_baseline(tmp_path,
                               baseline={},
                               current={"new.sysml": "part { attribute n; }"})
    fx = _fixtures_for({"new.sysml": "part { attribute n; }"})
    verdicts = vcor.compute_verdicts(cd, recorded=fx)
    assert vcor.ratchet_regressions(cd, verdicts) == []
