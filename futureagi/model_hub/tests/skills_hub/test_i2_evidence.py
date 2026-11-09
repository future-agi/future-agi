"""Release evidence records the pre-transition state on every legacy path."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from model_hub.models.skills_hub_v11 import HubSkillTestEvidence
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11 import jobs
from model_hub.services.skills_hub.v11.test_runs import share_test_run
from model_hub.tests.skills_hub import test_v11_jobs as fixtures

pytestmark = pytest.mark.django_db(transaction=True)
execution = fixtures.execution


def terminal_evidence(execution, publish_skill):
    principal, skill, config, _ = execution
    baseline = publish_skill(skill, labels=["production"]).version
    files = [
        {**file, "content": file["content"] + "\nReviewed synthetic amendment."}
        for file in skill.drafts.get(user_id=principal.user_id).files
    ]
    candidate = publish_skill(skill, files=files).version
    config["candidate"] = {
        "kind": "version",
        "version": candidate.number,
        "digest": candidate.digest,
    }
    config["baselines"] = ["production"]
    run, _, _ = fixtures.accepted(execution)
    jobs.finish_job(scope=principal.scope, job_id=run.pk)
    return principal, skill, baseline, candidate, files, run


def test_combined_publication_retains_pre_move_currentness_for_every_attachment(
    execution, publish_skill
):
    principal, skill, baseline, candidate, files, run = terminal_evidence(
        execution, publish_skill
    )
    share_test_run(
        principal=principal, run_id=run.pk, acknowledge_snapshot_sharing=True
    )
    result = publish_skill(
        skill, files=files, labels=["production", "staging"], evidence_run_id=run.pk
    )
    assert result.unchanged and result.version.pk == candidate.pk
    rows = list(HubSkillTestEvidence.objects.filter(run=run))
    assert len(rows) == 3
    assert all(row.currentness_at_action["baseline"] is True for row in rows)
    assert skill.labels.get(name="production").version_id == candidate.pk
    assert baseline.pk != candidate.pk
    assert result.evidence["currentness"]["baseline"] is True


def test_private_evidence_fails_before_allocating_a_version(execution, publish_skill):
    _, skill, _, candidate, files, run = terminal_evidence(execution, publish_skill)
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(SkillsHubError) as error:
            publish_skill(
                skill,
                files=[
                    {**file, "content": file["content"] + "Changed again"}
                    for file in files
                ],
                evidence_run_id=run.pk,
            )
    assert error.value.code == "evidence_share_required"
    assert not any(
        'INSERT INTO "model_hub_hubskillversion"' in query["sql"] for query in queries
    )
    skill.refresh_from_db()
    assert skill.latest_version_number == candidate.number
