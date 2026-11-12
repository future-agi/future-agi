"""Shared fixtures: the example catalog, generated once per session, and
per-test copies of single apps to break on purpose.

Nothing here reaches the network: every spec is read from
tests/fixtures/specs, pinned in SOURCES.json."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import support
import yaml

GENERATED = "tools.generated.json"


def provider_dir(catalog: Path, app: str) -> Path:
    return catalog / "apps" / app / "providers" / "futureagi-rest"


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, doc: Any) -> None:
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")


def edit_overlay(catalog: Path, app: str, change: Callable[[dict], None]) -> None:
    """Load an app's tools.yaml, let change mutate it, write it back."""
    path = provider_dir(catalog, app) / "tools.yaml"
    doc = read_yaml(path)
    change(doc)
    write_yaml(path, doc)


def edit_record(path: Path, change: Callable[[dict], None]) -> None:
    doc = read_yaml(path)
    change(doc)
    write_yaml(path, doc)


def tool(doc: dict, name: str) -> dict:
    """The overlay entry (or generated tool) called name."""
    for t in doc["tools"]:
        if t["name"] == name:
            return t
    raise KeyError(name)


def golden(app: str) -> dict:
    return json.loads((provider_dir(support.CATALOG, app) / GENERATED).read_text(encoding="utf-8"))


def output(catalog: Path, app: str) -> dict:
    return json.loads((provider_dir(catalog, app) / GENERATED).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def generated(tmp_path_factory) -> Path:
    """The whole example catalog with no outputs, generated once: the
    returned catalog holds what generate.py wrote for every app."""
    catalog = support.materialize(tmp_path_factory.mktemp("all"))
    for stale in catalog.glob(f"apps/*/providers/*/{GENERATED}"):
        stale.unlink()
    code, out, err = support.run("--catalog", str(catalog))
    assert code == 0, out + err
    return catalog


@pytest.fixture
def app_catalog(tmp_path) -> Callable[..., Path]:
    """A fresh copy of the named apps (with their reviewed outputs)."""

    def make(*apps: str) -> Path:
        return support.materialize(tmp_path, list(apps))

    return make


def run_ok(catalog: Path, *args: str) -> str:
    code, out, err = support.run("--catalog", str(catalog), *args)
    assert code == 0, out + err
    return out


def run_refused(catalog: Path, *args: str) -> str:
    """Run expecting exit 2 (a problem); return stderr."""
    code, out, err = support.run("--catalog", str(catalog), *args)
    assert code == 2, f"exit {code}\n{out}{err}"
    return err
