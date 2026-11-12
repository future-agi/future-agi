"""The CI job definition for this generator (scripts/connector-gen/ci/
connector-gen.yml): it runs the tests, with Go so the catalog cross-check
cannot skip, and generate.py --check over the repository catalog, so a
hand-edited tools.generated.json (an x-origin pointed at another host, a
posture loosened) fails the build. GitHub runs it once it is copied to
.github/workflows/connector-gen.yml (an integrator request)."""

from __future__ import annotations

import re

import support
import yaml

JOB_FILE = support.HERE.parents[1] / "ci" / "connector-gen.yml"
REPO = support.HERE.parents[3]
TESTS = "python3 -m pytest scripts/connector-gen/rest/tests -q"
CHECK = "python3 scripts/connector-gen/rest/generate.py --check"
DESTINATION = ".github/workflows/connector-gen.yml"
TRIGGER_PATHS = (
    "scripts/connector-gen/**",  # the generator, its tests and this job
    "agentcc-gateway/catalog/connectors/**",  # where a rest provider or a hand edit lands
    "agentcc-gateway/cmd/agentcc-catalog/**",  # the Go cross-check's command
    "agentcc-gateway/internal/connectorcatalog/**",  # and the loader and L1 it runs
    DESTINATION,
)


def _doc() -> dict:
    return yaml.safe_load(JOB_FILE.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    (job,) = _doc()["jobs"].values()
    return job["steps"]


def _step_running(command: str) -> dict:
    found = [s for s in _steps() if s.get("run", "").strip() == command]
    assert len(found) == 1, f"no single step runs {command!r}"
    return found[0]


def test_ci_job_runs_the_tests_and_the_drift_check_from_the_repo_root():
    for command in (TESTS, CHECK):
        step = _step_running(command)
        assert "working-directory" not in step, command  # both paths are repo-relative


def test_ci_job_drift_check_runs_even_when_a_test_fails():
    assert _step_running(CHECK).get("if") == "${{ !cancelled() }}"


def test_ci_job_has_python_and_go_so_the_cross_check_cannot_skip():
    (job,) = _doc()["jobs"].values()
    assert job["env"]["CONNECTOR_GEN_REQUIRE_GO"] == "1"
    uses = {s["uses"].split("@")[0]: s for s in _steps() if "uses" in s}
    assert uses["actions/setup-python"]["with"]["python-version"] == "3.12"
    assert uses["actions/setup-go"]["with"]["go-version-file"] == "agentcc-gateway/go.mod"
    installs = [s["run"] for s in _steps() if "pip install" in s.get("run", "")]
    assert len(installs) == 1 and "pytest==" in installs[0] and "PyYAML==" in installs[0]
    order = [s.get("uses", s.get("run", "")).split("@")[0] for s in _steps()]
    assert order.index("actions/setup-go") < order.index(TESTS)


def test_ci_job_triggers_on_the_generator_and_the_catalog():
    doc = _doc()
    on = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` key as true
    for event in ("pull_request", "push"):
        paths = on[event]["paths"]
        for want in TRIGGER_PATHS:
            assert want in paths, (event, want)


def test_ci_job_is_read_only_and_holds_no_secret():
    doc = _doc()
    assert doc["permissions"] == {"contents": "read"}
    checkout = [s for s in _steps() if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkout) == 1 and checkout[0]["with"]["persist-credentials"] is False
    assert not re.search(r"\$\{\{[^}]*\bsecrets\b", JOB_FILE.read_text(encoding="utf-8"))


def test_ci_job_names_where_it_must_be_copied():
    assert DESTINATION in JOB_FILE.read_text(encoding="utf-8")


def test_the_workflow_github_runs_is_this_job_byte_for_byte():
    # IR-CN4b-1 and the CN4b review P3: GitHub runs only the copy under .github/workflows, so an edit to
    # that copy alone (dropping the --check step, CONNECTOR_GEN_REQUIRE_GO or the !cancelled() guard)
    # would pass every test above, which read this file. The two copies must be the same bytes.
    workflow = REPO / DESTINATION
    assert workflow.is_file(), f"{DESTINATION} is missing: GitHub runs no copy of this job"
    assert workflow.read_bytes() == JOB_FILE.read_bytes(), f"{DESTINATION} differs from {JOB_FILE.name}"
