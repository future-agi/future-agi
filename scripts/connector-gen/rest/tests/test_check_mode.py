"""--check: report drift between each tools.generated.json and what its
tools.yaml and pinned spec generate, and write nothing."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import support
from restgen import cli
from conftest import edit_overlay, provider_dir, run_ok, run_refused, tool

GOLDEN = "apps/gmail/providers/futureagi-rest/tools.generated.json"


def _check(catalog, *args):
    return support.run("--catalog", str(catalog), "--check", *args)


def test_check_mode_up_to_date_exits_zero(app_catalog):
    catalog = app_catalog("gmail", "slack")
    code, out, err = _check(catalog)
    assert (code, err) == (0, "")
    assert f"ok       {GOLDEN} (10 tools)" in out


def test_check_mode_reports_drift_and_writes_nothing(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_profile").update(title="Get the profile"))
    target = catalog / GOLDEN
    before = target.read_bytes()
    code, out, err = _check(catalog)
    assert code == 1, out + err
    assert f"stale    {GOLDEN}: differs from what tools.yaml and the pinned spec generate; run generate.py" in out
    assert target.read_bytes() == before


def test_check_mode_missing_output_is_stale(app_catalog):
    catalog = app_catalog("gmail")
    (catalog / GOLDEN).unlink()
    code, out, _ = _check(catalog)
    assert code == 1 and f"stale    {GOLDEN}" in out
    assert not (catalog / GOLDEN).exists()


def test_check_mode_hand_edited_output_is_drift(app_catalog):
    catalog = app_catalog("gmail")
    doc = json.loads((catalog / GOLDEN).read_text())
    doc["tools"][0]["posture"] = "ask"
    (catalog / GOLDEN).write_text(json.dumps(doc))
    code, out, _ = _check(catalog)
    assert code == 1 and "stale" in out


def test_check_mode_hand_edited_origin_is_drift(app_catalog):
    # The CN4 review's repro: every x-origin in a generated gmail output
    # pointed at another host. agentcc-catalog check accepts that file; the
    # CI job's --check is what refuses it (exit 1).
    catalog = app_catalog("gmail")
    doc = json.loads((catalog / GOLDEN).read_text())
    for t in doc["tools"]:
        t["inputSchema"]["x-origin"] = "https://attacker.example"
    (catalog / GOLDEN).write_text(json.dumps(doc))
    code, out, err = _check(catalog)
    assert code == 1, out + err
    assert f"stale    {GOLDEN}: differs from what tools.yaml and the pinned spec generate; run generate.py" in out


def test_check_mode_ignores_layout(app_catalog):
    # The commit hook reformats JSON; only the data is compared.
    catalog = app_catalog("gmail")
    doc = json.loads((catalog / GOLDEN).read_text())
    (catalog / GOLDEN).write_text(json.dumps(doc, indent=4, sort_keys=True))
    code, out, _ = _check(catalog)
    assert code == 0, out


def test_check_mode_spec_drift_is_a_problem(app_catalog):
    catalog = app_catalog("gmail")
    spec = provider_dir(catalog, "gmail") / "fixtures/spec/gmail-v1.discovery.json.gz"
    spec.write_bytes(support.SPECS.joinpath("calendar-v3.discovery.json.gz").read_bytes())
    code, out, err = _check(catalog)
    assert code == 2
    assert "sha256 of the spec bytes is e0a0bdacc2879f7777e6adb0f400f01b19f949160946b36113624c3eae1c17b9" in err


def test_check_mode_orphan_output_reported(app_catalog):
    catalog = app_catalog("gmail")
    (provider_dir(catalog, "gmail") / "tools.yaml").unlink()
    code, _, err = _check(catalog)
    assert code == 2
    assert f"{GOLDEN}: has no tools.yaml beside it, so nothing can regenerate it" in err


def test_check_mode_unknown_app_refused(app_catalog):
    catalog = app_catalog("gmail")
    err = run_refused(catalog, "--check", "--app", "gmial")
    assert "no rest overlay (apps/<app>/providers/<provider>/tools.yaml) for gmial" in err


def test_check_mode_app_filter_checks_only_that_app(app_catalog):
    catalog = app_catalog("gmail", "slack")
    (catalog / "apps/slack/providers/futureagi-rest/tools.generated.json").unlink()
    code, out, _ = _check(catalog, "--app", "gmail")
    assert code == 0 and "slack" not in out


def test_check_mode_after_write_passes(app_catalog):
    catalog = app_catalog("gmail")
    edit_overlay(catalog, "gmail", lambda d: tool(d, "get_profile").update(title="Get the profile"))
    out = run_ok(catalog)
    assert f"wrote    {GOLDEN} (10 tools)" in out
    assert tool(json.loads((catalog / GOLDEN).read_text()), "get_profile")["title"] == "Get the profile"
    assert _check(catalog)[0] == 0


def test_check_mode_as_a_process(app_catalog):
    catalog = app_catalog("gmail")
    script = support.HERE.parent / "generate.py"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    ok = subprocess.run([sys.executable, str(script), "--check", "--catalog", str(catalog)],
                        capture_output=True, text=True, env=env, timeout=300)  # fmt: skip
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (catalog / GOLDEN).unlink()
    stale = subprocess.run([sys.executable, str(script), "--check", "--catalog", str(catalog)],
                           capture_output=True, text=True, env=env, timeout=300)  # fmt: skip
    assert stale.returncode == 1 and "stale" in stale.stdout


def test_check_mode_on_the_repo_catalog():
    # The repository's own catalog holds rest providers (CN14c: gmail and
    # google-calendar; CN15c: microsoft-outlook and microsoft-teams): --check
    # compares each committed tools.generated.json with what its tools.yaml
    # and pinned spec generate, and passes without failing on the other kinds.
    code, out, err = support.run("--check")
    assert (code, err) == (0, ""), out + err
    overlays = sorted(cli.DEFAULT_CATALOG.glob("apps/*/providers/*/tools.yaml"))
    assert overlays, "the repository catalog has no rest overlay"
    checked = [line for line in out.splitlines() if line.startswith("ok ")]
    assert len(checked) == len(overlays), out
