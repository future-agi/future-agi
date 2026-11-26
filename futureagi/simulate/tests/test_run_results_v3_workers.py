"""The served workers record: a cancel the job has since confirmed is served as cancelled; the stored record stays."""

import pytest

from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_workers import DECLARED, RUN, SPANS, _cut_short_run


@pytest.mark.django_db
def test_cancel_requested_is_served_cancelled_once_the_job_is_canceled(
    user, workspace, storage, settings, auth_client
):
    from simulate.models import CallExecution, HostedHarnessJob
    from simulate.services.harness_worker_timeline import worker_timeline
    from simulate.services.harness_workers import WORKERS_KEY
    from simulate.services.scoring_status import locked_call_metadata_update

    job, _attempt, call = _cut_short_run(user, workspace, storage, settings, "conf")
    timeline = {
        **worker_timeline(SPANS, DECLARED, run_key=RUN),
        "collected_at": "2026-10-31T12:00:00Z",
    }
    timeline["workers"] = [
        {**one, "state": "cancel_requested"} for one in timeline["workers"]
    ]
    locked_call_metadata_update(
        call.id, lambda metadata: metadata.update({WORKERS_KEY: timeline}) or True
    )
    CallExecution.objects.filter(id=call.id).update(
        status=CallExecution.CallStatus.CANCELLED
    )
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        state=HostedHarnessJob.State.CANCELED
    )

    found = auth_client.get(f"/simulate/v3/call-executions/{call.id}/workers/")

    assert found.status_code == 200, found.content
    assert {one["state"] for one in found.json()["workers"]} == {"cancelled"}
    call.refresh_from_db()
    assert {one["state"] for one in call.call_metadata[WORKERS_KEY]["workers"]} == {
        "cancel_requested"
    }
