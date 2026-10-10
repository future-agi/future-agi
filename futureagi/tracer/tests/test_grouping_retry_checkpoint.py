"""Checkpoint invalidation across independently counted cohort retries."""

import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from tracer.models.trace_grouping import (
    TraceGroupingAttempt,
    TraceGroupingScope,
    TraceGroupingWork,
)
from tracer.services.grouping.control import (
    checkpoint_attempt,
    claim_grouping_work,
    update_grouping_attempt,
)
from tracer.services.grouping.publish import _assign, _new_issue
from tracer.tests.test_grouping_runtime import FakeFeatureStore, _prepare_runtime

pytestmark = pytest.mark.django_db


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="100",
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="100",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="100",
)
def test_peer_participation_does_not_skip_candidate_checkpoint_invalidation(
    observe_project, monkeypatch
):
    owned = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    scope = TraceGroupingScope.no_workspace_objects.get(project=observe_project)
    owned_id = str(owned.findings.get().id)
    issue = _new_issue(
        scope,
        {
            "mechanism": "Wrong refund amount",
            "fix_hypothesis": "Use requested amount",
            "falsifier": "Executed amount matches",
        },
        [owned_id],
    )
    _assign(owned.findings.get(), issue, scope)
    owned.grouping_status = "completed"
    owned.save(update_fields=["grouping_status"])
    TraceGroupingWork.no_workspace_objects.filter(report=owned).update(
        state="completed"
    )

    report = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    first = claim_grouping_work(worker_id="first-lead", limit=1)["claims"][0]
    assert first["candidate_window"]["issues"] == []
    saved = {"files": {"checkpoint.json": {"binding": "previous-candidate-window"}}}
    checkpoint_attempt(
        attempt_id=first["attempt_id"],
        lease_token=first["lease_token"],
        expected_revision=first["checkpoint_revision"],
        checkpoint=saved,
    )
    update_grouping_attempt(
        attempt_id=first["attempt_id"],
        lease_token=first["lease_token"],
        action="fail",
        failure_code="control_timeout",
    )

    other = _prepare_runtime(observe_project, monkeypatch, identity=uuid.uuid4())
    now = timezone.now()
    TraceGroupingWork.no_workspace_objects.filter(report=other).update(
        attempt_number=4, not_before=now - timedelta(seconds=2)
    )
    TraceGroupingWork.no_workspace_objects.filter(report=report).update(
        not_before=now - timedelta(seconds=1)
    )
    middle = claim_grouping_work(worker_id="other-lead", limit=1)["claims"][0]
    assert middle["report_id"] == str(other.id)
    assert {snap["report"]["id"] for snap in middle["pending_snapshots"]} == {
        str(other.id),
        str(report.id),
    }
    update_grouping_attempt(
        attempt_id=middle["attempt_id"],
        lease_token=middle["lease_token"],
        action="fail",
        failure_code="control_timeout",
    )
    assert TraceGroupingWork.no_workspace_objects.get(report=other).state == "failed"
    work = TraceGroupingWork.no_workspace_objects.get(report=report)
    assert work.attempt_number == 2
    assert list(work.attempts.values_list("attempt_number", flat=True)) == [1]

    # An existing issue becomes visible in the index without a registry edit.
    monkeypatch.setattr(
        FakeFeatureStore, "candidate_occurrences", lambda self, **kwargs: [owned_id]
    )
    TraceGroupingWork.no_workspace_objects.filter(report=report).update(not_before=now)
    latest = claim_grouping_work(worker_id="original-lead", limit=1)["claims"][0]
    assert latest["snapshot_digest"] == first["snapshot_digest"]
    assert latest["registry_revision"] == first["registry_revision"]
    assert latest["candidate_digest"] != first["candidate_digest"]
    assert latest["candidate_window"]["issues"][0]["issue_id"] == str(issue.cluster_id)
    assert latest["checkpoint"] == {}
    assert latest["checkpoint_revision"] == 0
    attempt = TraceGroupingAttempt.no_workspace_objects.get(pk=latest["attempt_id"])
    assert attempt.attempt_number == 3
    original = TraceGroupingAttempt.no_workspace_objects.get(pk=first["attempt_id"])
    assert original.checkpoint == saved
