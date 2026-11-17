"""The generator's output is what the gateway's catalog accepts: the
example catalog, with every tools.generated.json the generator wrote,
passes `agentcc-catalog check --sources-only` (CN1's strict loader, L0, L1
and CN6's emitters), and the check still catches a breach.

Needs go. Without it the cross-check is skipped, unless
CONNECTOR_GEN_REQUIRE_GO is set (the CI job sets it), when it fails."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
import support
from conftest import provider_dir, tool

GATEWAY = support.HERE.parents[3] / "agentcc-gateway"
REQUIRE_GO = "CONNECTOR_GEN_REQUIRE_GO"  # set by the CI job (ci/connector-gen.yml)


def _catalog_check(catalog):
    if shutil.which("go") is None:
        if os.environ.get(REQUIRE_GO):
            pytest.fail(f"go is not installed, and {REQUIRE_GO} says this cross-check must run")
        pytest.skip("go is not installed")
    return subprocess.run(
        ["go", "run", "./cmd/agentcc-catalog", "check", "--catalog", str(catalog), "--sources-only", "--django-copy", ""],
        cwd=GATEWAY, capture_output=True, text=True, timeout=900,
    )  # fmt: skip


@pytest.fixture(scope="module")
def checked(generated, tmp_path_factory):
    catalog = tmp_path_factory.mktemp("go") / "catalog"
    shutil.copytree(generated, catalog)
    return catalog


def test_catalog_check_skips_without_go_locally(monkeypatch):
    monkeypatch.delenv(REQUIRE_GO, raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(pytest.skip.Exception):
        _catalog_check(support.CATALOG)


def test_catalog_check_fails_without_go_in_ci(monkeypatch):
    # The CI job sets CONNECTOR_GEN_REQUIRE_GO, so a runner without go fails
    # the build instead of passing with the cross-check skipped.
    monkeypatch.setenv(REQUIRE_GO, "1")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    try:
        _catalog_check(support.CATALOG)
    except pytest.fail.Exception as exc:
        assert "go is not installed" in str(exc)
    except pytest.skip.Exception:
        pytest.fail(f"the cross-check skipped although {REQUIRE_GO} is set")
    else:
        pytest.fail("the cross-check ran with no go on PATH")


def test_generated_catalog_passes_the_gateway_catalog_check(checked):
    done = _catalog_check(checked)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == "ok"


def test_gateway_catalog_check_still_catches_a_breach(checked, tmp_path):
    catalog = tmp_path / "catalog"
    shutil.copytree(checked, catalog)
    target = provider_dir(catalog, "gmail") / "tools.generated.json"
    doc = json.loads(target.read_text())
    tool(doc, "send_message")["posture"] = "auto"
    target.write_text(json.dumps(doc))
    done = _catalog_check(catalog)
    assert done.returncode == 1
    assert "tool send_message writes, so its posture is ask or deny, not auto" in done.stderr
