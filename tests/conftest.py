"""Shared pytest fixtures for the codegen test-suite (issue #9)."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_sysml2py_module():
    """Purge any cached ``sysml2py`` module between tests (r7 W2).

    Without this, the first test to import the generated package caches
    ``sys.modules["sysml2py"]`` and every later test silently binds to that
    first tmp_tree — the per-test ``sys.path`` inserts become inert.  Each
    test here generates its own tree, so each must import its own copy.
    """
    sys.modules.pop("sysml2py", None)
    yield
    sys.modules.pop("sysml2py", None)
