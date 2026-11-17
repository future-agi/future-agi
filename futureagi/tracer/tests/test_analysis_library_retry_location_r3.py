"""Installed retry source discovery must not depend on a venv layout."""

import builtins
import importlib.util
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "layout",
    [
        "lib/python3.11/site-packages",
        "lib64/python3.11/site-packages",
        "relocated-packages",
    ],
)
def test_retry_fixture_uses_installed_origin_without_importing_litellm(
    monkeypatch, tmp_path, layout
):
    origin = tmp_path / layout / "litellm" / "__init__.py"
    find_spec = Mock(return_value=SimpleNamespace(origin=str(origin)))
    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "litellm" or name.startswith("litellm."):
            raise AssertionError("Source discovery imported litellm")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    fixture = runpy.run_path(
        str(Path(__file__).with_name("analysis_library_retry_fixture.py"))
    )

    assert fixture["SITE"] == origin.parent
    find_spec.assert_called_once_with("litellm")
