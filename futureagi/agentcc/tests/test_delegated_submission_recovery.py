"""A process exit at the submission boundary cannot strand a live grant."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from accounts.models.service_grant import ServiceGrant
from agentcc.models import AgentccBatchJob
from agentcc.services.batch_jobs import poll_open_batches
from agentcc.services.delegated_batches import submit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("accepted", [True, False])
def test_worker_death_is_reconciled_without_exception_handler(
    user, workspace, accepted, settings
):
    settings.SECRET_KEY = "test_only_recovery_signing_pair_0000000000"
    client = MagicMock()
    captured = []

    def die(delegation, requests, concurrency):
        from django.db import connection

        from accounts.models.service_grant import BatchSubmissionIntent

        assert not connection.in_atomic_block
        assert BatchSubmissionIntent.objects.filter(
            grant_id=delegation.claims["id"], resolved_at__isnull=True
        ).exists()
        captured.append(delegation)
        raise SystemExit("simulated worker exit")  # deliberately not Exception

    client.submit_delegated_batch.side_effect = die
    with pytest.raises(SystemExit):
        submit(
            client,
            organization=user.organization,
            user=user,
            workspace=workspace,
            requests=[{"model": "test/model"}],
            max_concurrency=1,
        )
    grant = ServiceGrant.objects.get(pk=captured[0].claims["id"])
    job = AgentccBatchJob.objects.get(pk=grant.batch_ref)
    # Run the real periodic entry point, not a helper invoked by the exception path.
    client.list_delegated_batches.return_value = {
        "batches": [
            {
                "batch_id": "accepted-before-exit",
                "org_id": str(user.organization_id),
                "key_id": "dlg_" + grant.pk,
            }
        ]
        if accepted
        else []
    }
    client.get_batch.return_value = {"status": "completed", "total": 1}
    future = timezone.now() + timedelta(minutes=3)
    with patch("django.utils.timezone.now", return_value=future):
        poll_open_batches(client)
    grant.refresh_from_db()
    job.refresh_from_db()
    assert job.notified is True
    assert grant.revoked_at is not None
    if accepted:
        assert job.gateway_batch_id == "accepted-before-exit"
        assert job.status == AgentccBatchJob.COMPLETED
    else:
        assert job.status == AgentccBatchJob.FAILED
    client.submit_delegated_batch.assert_called_once()  # no duplicate submit
    client.create_key.assert_not_called()
