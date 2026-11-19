"""Validate proves a case on the pair's sealed runtime, as a build does; a build's own live proofs travel with its
suite; a control that never ran shows no verdict; and an install with no hosted runtime answers Validate cleanly."""

import json

import pytest

from simulate.models import HostedHarnessJob
from simulate.services.harness_scenario_lifecycle import scenario_lifecycle
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.versions import (
    build_proofs,
    control_ran,
    record_proofs,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_scenario_lifecycle_transitions import CASE, KEYS, PROVED, _base

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]

LIVE = {
    "format": 2,
    "holds": True,
    "proved": True,
    "live": True,
    "proved_at": "2099-01-01T00:00:00Z",
}
OFFLINE = {
    "format": 2,
    "holds": True,
    "proved": False,
    "kept_as_written": True,
    "live": False,
    "blocked_reason": "solution_assumed",
    "assumed": ["issue_refund"],
}


def _state(environment):
    return scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )[CASE]


def test_a_live_proof_from_prove_only_lands_as_proved_and_active(
    user, workspace, storage
):
    environment = _base(user, workspace, storage, "live-proof")
    record_proofs(
        environment.suite_version,
        environment.environment_version,
        {**dict.fromkeys(KEYS, PROVED), CASE: LIVE},
        environment,
    )

    view = _state(environment)

    assert (view["state"], view["proof"]["proved"]) == ("active", True)


def test_an_older_offline_only_proof_is_still_read_as_it_was(user, workspace, storage):
    environment = _base(user, workspace, storage, "offline-proof")
    record_proofs(
        environment.suite_version,
        environment.environment_version,
        {**dict.fromkeys(KEYS, PROVED), CASE: OFFLINE},
        environment,
    )

    view = _state(environment)

    assert view["state"] == "kept_as_written"
    assert "could not be run here (issue_refund)" in view["warning_text"]


def test_a_builds_live_proofs_travel_with_its_suite_over_its_offline_ones():
    assert "scenario-proofs.json" in archives.SUITE_FILES
    suite = {
        "proofs.json": json.dumps({"refund-1": OFFLINE, "refund-2": OFFLINE}).encode(),
        "scenario-proofs.json": json.dumps({"refund-1": LIVE}).encode(),
    }
    assert build_proofs(suite) == {"refund-1": LIVE, "refund-2": OFFLINE}
    assert build_proofs({"proofs.json": b"not json"}) == {}


@pytest.mark.parametrize(
    "control, ran",
    [
        ({"name": "two refunds", "ran": False, "failed": None}, False),
        ({"name": "two refunds", "ran": True, "failed": True}, True),
        # Saved before ALK wrote `ran`: a verdict means it ran, an empty one that it did not.
        ({"name": "two refunds", "failed": True}, True),
        ({"name": "two refunds", "failed": False}, True),
        ({"name": "two refunds", "failed": None}, False),
    ],
)
def test_a_control_ran_only_when_alk_says_so_or_left_a_verdict(control, ran):
    assert control_ran(control) is ran


def test_a_control_that_never_ran_shows_no_verdict(user, workspace, storage):
    environment = _base(user, workspace, storage, "control-not-run")
    unrun = {
        **OFFLINE,
        "blocked_reason": "control_not_run",
        "controls": [
            {
                "name": "two refunds",
                "why": "refunds twice",
                "ran": False,
                "failed": None,
                "assumed": ["issue_refund"],
            }
        ],
    }
    record_proofs(
        environment.suite_version,
        environment.environment_version,
        {**dict.fromkeys(KEYS, PROVED), CASE: unrun},
        environment,
    )

    [control] = _state(environment)["proof"]["controls"]

    assert (control["ran"], control["failed"]) == (False, None)


def test_validate_on_an_install_with_no_hosted_runtime_answers_cleanly(
    user, workspace, storage, auth_client, settings
):
    environment = _base(user, workspace, storage, "no-hosted-runtime")
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    jobs = HostedHarnessJob.no_workspace_objects.count()

    response = auth_client.post(
        f"/simulate/api/harness-environments/{environment.id}/scenarios/validate/",
        {},
        format="json",
    )

    assert response.status_code == 422, response.content
    assert response.json()["error"] == "cannot_validate"
    assert "can't be validated here" in response.json()["message"]
    assert response.json()["retryable"] is False
    assert HostedHarnessJob.no_workspace_objects.count() == jobs


def test_the_newer_of_a_live_and_an_offline_proof_is_kept_per_case():
    """A live file from an earlier certify never hides a newer offline proof; on equal or missing times live wins."""
    older_live = {**LIVE, "proved_at": "2099-01-01T00:00:00Z"}
    newer_offline = {**OFFLINE, "proved_at": "2099-01-02T00:00:00Z"}
    untimed_live = {k: v for k, v in LIVE.items() if k != "proved_at"}
    suite = {
        "proofs.json": json.dumps(
            {
                "a": newer_offline,
                "b": newer_offline,
                "c": {**OFFLINE, "proved_at": "2099-01-01T00:00:00Z"},
            }
        ).encode(),
        "scenario-proofs.json": json.dumps(
            {"a": older_live, "b": untimed_live, "c": older_live}
        ).encode(),
    }

    proofs = build_proofs(suite)

    assert proofs["a"] == newer_offline
    assert proofs["b"] == untimed_live
    assert proofs["c"] == older_live


@pytest.mark.parametrize(
    "laid, takes_flag, given",
    [(True, True, True), (False, True, False), (True, False, False)],
    ids=["document-and-flag", "no-document", "older-harness"],
)
def test_proving_is_given_the_agent_document_only_when_it_is_there_and_the_harness_takes_it(
    tmp_path, laid, takes_flag, given
):
    """`--agent-document` reaches certify and prove-only when the document is laid and the harness lists the flag;
    an older harness never sees an argument it would refuse."""
    import os
    import subprocess

    from simulate.services.harness_versioning import assembly

    document = tmp_path / "agent.json"
    if laid:
        document.write_text("{}")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "python"
    # The harness's own help, from the module the probe names only: any other module prints nothing.
    fake.write_text(
        '#!/bin/sh\nif [ "$1 $2 $3" = "-m fi.alk.harness.authoring_entrypoint --help" ]; then echo \''
        + ("--agent-document PATH" if takes_flag else "--prove-only")
        + "'; fi\n"
    )
    fake.chmod(0o755)
    fragment = assembly.AGENT_DOCUMENT_FLAG.replace(
        assembly.AGENT_READING_PATH, str(document)
    )

    out = subprocess.run(
        ["/bin/sh", "-c", f"printf '%s' \"{fragment}\""],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        check=True,
    ).stdout

    assert out == (f" --agent-document {document}" if given else "")
    assert assembly.AGENT_DOCUMENT_FLAG in assembly.certify_command(prove=True)
    assert assembly.AGENT_DOCUMENT_FLAG in assembly.prove_suite_command()


def test_the_probe_asks_the_entrypoint_that_parses_before_anything_else():
    """`authoring_entrypoint` builds its parser first, so --help has no side effects; the hosted entrypoint sets up
    isolation and credential files before it parses, and is never the one asked."""
    from simulate.services.harness_versioning import assembly

    assert assembly.AGENT_DOCUMENT_PROBE == "fi.alk.harness.authoring_entrypoint"
    assert (
        f"python -m {assembly.AGENT_DOCUMENT_PROBE} --help"
        in assembly.AGENT_DOCUMENT_FLAG
    )
    assert "hosted_authoring_entrypoint" not in assembly.AGENT_DOCUMENT_FLAG
