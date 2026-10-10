"""Issue #12 — Windtrader validation adapter.

A thin adapter around Tock Ratchetpin's `windtrader` Python wrapper (which
drives the `windtrader-java` SysML v2 validator as an external subprocess).

Design (per validate/protocol.md)
---------------------------------
* Windtrader is the INDEPENDENT correctness oracle.  The lab must never
  validate its own assumptions — only windtrader-java's verdict counts.
* Invocation: `windtrader.validate(text, version=...)` via the wrapper's
  public API (CLI-check equivalent).  The wrapper auto-downloads and caches
  the pinned jar (default 0.2.0), so network + jar resolution live there.
* Exit-code contract (from windtrader-java, preserved by the wrapper):
      0  valid syntax
      2  invalid SysML (parse error)
      3  runtime / tool failure (and anything not 0/2)
* Adapter failures (ImportError, no java, timeout, unexpected exception) are
  DISTINGUISHABLE from model-invalid verdicts: they surface as
  ``Verdict(status="adapter_error", ...)``, never as ``status="invalid"``.
* Offline / hermetic mode: when ``recorded`` dicts are supplied (or a
  fixtures dir is configured), the adapter REPLAYS recorded verdicts keyed
  by a stable digest of (text, version) and never touches the jar.  This
  keeps the lab's unit tests hermetic and lets CI run without Java when the
  fixtures are committed.

The committed ``corpus/validate-fixtures.json`` is produced by
``corpus.build_fixture_dict`` (single source of truth; CLI ``--record`` and
the CI fixture-drift step both use it).  ``record_verdicts()`` below is the
library primitive the tests exercise; production recording routes through
``build_fixture_dict`` so shapes stay identical.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Version of the windtrader-java validator this adapter targets.
DEFAULT_VERSION = "0.2.0"

# Exit codes from windtrader-java's CLI contract.
EXIT_VALID = 0
EXIT_INVALID = 2

# Statuses the adapter emits.
STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_ADAPTER_ERROR = "adapter_error"


@dataclass(frozen=True)
class Verdict:
    """Normalized, tool-agnostic validation verdict for one text.

    Attributes
    ----------
    status:
        ``"valid"`` | ``"invalid"`` | ``"adapter_error"``.
    exit_code:
        Raw exit code from windtrader-java (0/2/other), or None if the
        adapter failed before the tool ran.
    version:
        windtrader-java version (or the recorded-fixture version).
    diagnostics:
        Normalized diagnostic lines (e.g. ``error: line=1 offset=14
        near=mass``) or an adapter-failure message.
    duration_s:
        Wall-clock time of the subprocess call (0.0 for replayed fixtures).
    recorded:
        True when this verdict was replayed from a fixture, not freshly
        produced by the validator.
    """

    status: str
    exit_code: int | None = None
    version: str = DEFAULT_VERSION
    diagnostics: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    recorded: bool = False

    @property
    def ok(self) -> bool:
        """True only for a genuine *valid* verdict (never adapter errors)."""
        return self.status == STATUS_VALID

    def to_dict(self) -> dict[str, Any]:
        # Shape matches corpus.build_fixture_dict exactly (status/exit_code/
        # version/diagnostics [+ file when provenance]).  The old "recorded"
        # key was dropped (r3 W2-G) — it was drift-incompatible with the
        # single production fixture builder.
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "version": self.version,
            "diagnostics": self.diagnostics,
        }


def digest_text(text: str, version: str = DEFAULT_VERSION) -> str:
    """Stable digest keying recorded fixtures: sha256(text + version)."""
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    h.update(b"\x00")
    h.update(version.encode("utf-8"))
    return h.hexdigest()


def _normalize_diagnostics(stderr: str) -> list[str]:
    """Normalize windtrader-java stderr into stable diagnosis lines.

    The Java tool prints lines like ``error: line=1 offset=14 near=mass``.
    We keep them verbatim (trimmed, non-empty) — they are already the
    canonical shape, and normalizing further risks losing location info.
    """
    return [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]


def normalize_result(result: Any, version: str = DEFAULT_VERSION) -> Verdict:
    """Map a windtrader-py ``CommandResult`` to a lab ``Verdict``.

    ``result`` may be any object exposing ``ok``/``exit_code``/``stderr``
    (duck-typed so the adapter is thin and testable without the package).
    """
    exit_code = getattr(result, "exit_code", None)
    stderr = getattr(result, "stderr", "") or ""
    if exit_code == EXIT_VALID:
        status = STATUS_VALID
    elif exit_code == EXIT_INVALID:
        status = STATUS_INVALID
    else:
        # 3 (runtime) or unexpected code -> tool failure, NOT model-invalid.
        status = STATUS_ADAPTER_ERROR
        stderr = stderr or f"windtrader-java exited {exit_code} (runtime/tool failure)"
    return Verdict(
        status=status,
        exit_code=exit_code,
        version=version,
        diagnostics=_normalize_diagnostics(stderr),
        duration_s=getattr(result, "duration_s", 0.0),
    )


class AdapterError(RuntimeError):
    """Raised when the adapter itself fails (import, java, timeout)."""


def _import_validate() -> Callable:
    """Import windtrader's `validate`, raising AdapterError on failure."""
    try:
        from windtrader import validate  # type: ignore[import-not-found]
    except Exception as exc:  # ImportError, dependency error, ...
        raise AdapterError(f"windtrader is not importable: {exc}") from exc
    return validate


def validate_text(
    text: str,
    *,
    version: str = DEFAULT_VERSION,
    recorded: dict[str, dict[str, Any]] | None = None,
) -> Verdict:
    """Validate one SysML text through windtrader-java.

    ``recorded`` maps digest_text(text, version) -> verdict dict; when the
    digest matches, replay the verdict WITHOUT invoking the jar (offline /
    hermetic mode).  A digest MISS never configures a replay (a missing
    fixture on a hermetic run is an adapter error, never a silent valid).
    """
    dig = digest_text(text, version)
    if recorded is not None:
        entry = recorded.get(dig)
        if entry is None:
            raise AdapterError(
                f"no recorded fixture for digest {dig[:12]}… (offline mode; "
                "record fixtures with `sysml2py-lab validate --record`)"
            )
        return Verdict(
            status=entry.get("status", STATUS_ADAPTER_ERROR),
            exit_code=entry.get("exit_code"),
            version=entry.get("version", version),
            diagnostics=list(entry.get("diagnostics", [])),
            recorded=True,
        )

    validate_fn = _import_validate()
    try:
        result = validate_fn(text, version=version)
    except FileNotFoundError as exc:
        raise AdapterError("java not found on PATH; windtrader-java requires a JVM") from exc
    except Exception as exc:  # subprocess failures, timeouts, ...
        raise AdapterError(f"windtrader invocation failed: {exc}") from exc
    return normalize_result(result, version=version)


def record_verdicts(
    text_by_name: dict[str, str],
    *,
    version: str = DEFAULT_VERSION,
    extra_texts: dict[str, str] | None = None,
    provenance: dict[str, str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Verdict]]:
    """Validate many texts and build a reusable recorded-fixture dict.

    Returns ``(fixtures, verdicts)`` where ``fixtures[digest]`` is the
    replayable dict and ``verdicts[name]`` is the live Verdict.  Any
    adapter error raises (recording must be real, never fudged — issue-#12
    r1 B3/W1-3).

    ``extra_texts`` maps an additional text (e.g. the round-trip emitted
    text) whose digest is ALSO recorded — keyed the same ``name`` so both
    sides of one file land under one provenance name.  ``provenance`` maps
    ``name -> manifest-relative path`` recorded as ``"file"`` on each
    fixture for auditability (W1-11).
    """
    fixtures: dict[str, dict[str, Any]] = {}
    verdicts: dict[str, Verdict] = {}
    names = sorted(set(text_by_name) | set(extra_texts or {}))
    for name in names:
        if name in text_by_name:
            v = validate_text(text_by_name[name], version=version)
            verdicts[name] = v
            if v.status == STATUS_ADAPTER_ERROR:
                raise AdapterError(
                    f"record failed for {name}: windtrader-java exited "
                    f"{v.exit_code}: {' '.join(v.diagnostics)[:200]}"
                )
            entry = v.to_dict()
            if provenance:
                entry["file"] = provenance[name]
            fixtures[digest_text(text_by_name[name], version)] = entry
        if extra_texts and name in extra_texts:
            rt = extra_texts[name]
            rv = validate_text(rt, version=version)
            if rv.status == STATUS_ADAPTER_ERROR:
                raise AdapterError(
                    f"record failed for {name} (round-trip): windtrader-java "
                    f"exited {rv.exit_code}"
                )
            entry = rv.to_dict()
            if provenance:
                entry["file"] = provenance[name]
            fixtures[digest_text(rt, version)] = entry
    return fixtures, verdicts


def load_fixtures(path: Path) -> dict[str, dict[str, Any]]:
    """Load a recorded-fixtures JSON file (missing file -> empty dict)."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_fixtures(path: Path, fixtures: dict[str, dict[str, Any]]) -> None:
    """Write a recorded-fixtures JSON file (sorted for determinism)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(fixtures, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
