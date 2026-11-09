"""Cutover refuses unsafe inputs and keeps retries reversible."""

import uuid
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models.retirement_manifest import RetirementManifest
from accounts.services.retirement_733 import GatewayAdapter, run_retirement
from accounts.services.retirement_fence import gateway_retirement_writes
from accounts.services.retirement_rows import reverse_rows
from accounts.services.service_grants import issue_person_grant
from agentcc.models import AgentccAPIKey, AgentccBatchJob
from agentcc.serializers.contracts import GatewayBatchDetailResultSerializer
from agentcc.services.batch_jobs import recorded_result

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def disabled_console(settings, monkeypatch):
    settings.SECRET_KEY = "test_only_cutover_signing_pair_00000000"
    for name in (
        "AGENTCC_DELEGATED_BATCH_ENABLED",
        "AGENTCC_DELEGATED_PLAYGROUND_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
        if hasattr(settings, name):
            delattr(settings, name)


@pytest.fixture
def retirement_dependencies():
    client = MagicMock()
    with (
        patch(
            "accounts.services.retirement_733.gateway_retirement",
            return_value=lambda *a, **k: {"selected": 0, "pending": []},
        ),
        patch(
            "accounts.services.retirement_733.repush_configs", return_value=(0, 0, {})
        ),
        patch(
            "agentcc.services.gateway_client.get_gateway_client", return_value=client
        ),
    ):
        yield client


def batch(user, workspace, *, pending=False):
    ref = uuid.uuid4()
    _, _, grant = issue_person_grant(
        kind="batch",
        org=user.organization_id,
        user=user.pk,
        workspace=workspace.pk,
        sub="batch:" + str(ref),
        ttl=86400,
    )
    job = AgentccBatchJob.objects.create(
        id=ref,
        organization=user.organization,
        created_by=user,
        gateway_batch_id=("pending-" if pending else "batch-") + str(ref),
        gateway_key_id="",
        total=1,
    )
    return job, grant


def test_two_forward_revokes_then_reverse_restores_first_state(user):
    row = AgentccAPIKey._base_manager.create(
        organization=user.organization,
        user=user,
        name="console-batch-test-only",
        gateway_key_id="test_only_retry",
        status="active",
    )
    for _ in range(2):
        # Match the operator's bounded adapter-write context, not a customer's
        # otherwise indistinguishable repeated revoke save.
        with gateway_retirement_writes(), transaction.atomic():
            key = AgentccAPIKey._base_manager.select_for_update().get(pk=row.pk)
            prior = {"status": key.status, "expires_at": None}
            key.status = "revoked"
            key.save(update_fields=["status", "updated_at"])
            GatewayAdapter().record(key=key, prior_state=prior, confirmed=True)
    manifest = RetirementManifest.objects.get(service="gateway")
    assert manifest.prior_state == {"status": "active", "expires_at": None}
    result = reverse_rows()
    row.refresh_from_db()
    assert result["restored"] == 1 and result["superseded"] == 0
    assert row.status == "active"


@pytest.mark.parametrize("batch_switch", [True, False])
def test_console_environment_switch_prevents_retirement(monkeypatch, batch_switch):
    from agentcc.services.delegated_availability import delegated_enabled

    name = (
        "AGENTCC_DELEGATED_BATCH_ENABLED"
        if batch_switch
        else "AGENTCC_DELEGATED_PLAYGROUND_ENABLED"
    )
    monkeypatch.setenv(name, "true")
    assert delegated_enabled(batch=batch_switch)
    assert not GatewayAdapter().issuance_is_off()
    with pytest.raises(CommandError, match="must be OFF"):
        run_retirement()


@pytest.mark.parametrize(
    "value,enabled", [(False, False), ("false", False), (True, True), ("true", True)]
)
def test_settings_precede_environment_for_both_readers(
    settings, monkeypatch, value, enabled
):
    from agentcc.services.delegated_availability import delegated_enabled

    monkeypatch.setenv("AGENTCC_DELEGATED_BATCH_ENABLED", "true")
    settings.AGENTCC_DELEGATED_BATCH_ENABLED = value
    assert delegated_enabled(batch=True) is enabled
    assert GatewayAdapter().issuance_is_off() is not enabled


def test_pending_delegated_submit_is_not_lost(user, workspace, retirement_dependencies):
    job, grant = batch(user, workspace, pending=True)
    run_retirement()
    job.refresh_from_db()
    grant.refresh_from_db()
    assert not job.notified and job.status == "processing"
    assert grant.revoked_at is None
    retirement_dependencies.get_batch.assert_not_called()


def test_open_legacy_batch_refuses_before_any_mutation(user, retirement_dependencies):
    job = AgentccBatchJob.objects.create(
        organization=user.organization,
        gateway_batch_id="test-only-legacy",
        gateway_key_id="test-only-legacy-key",
        total=1,
    )
    with pytest.raises(CommandError, match=r"1.*--accept-open-batch-loss"):
        call_command("retire_org_keys_733", stdout=StringIO())
    job.refresh_from_db()
    assert not job.notified
    assert RetirementManifest.objects.count() == 0


def test_explicit_loss_acceptance_reports_count_and_skips_delegated(
    user, workspace, retirement_dependencies
):
    legacy = AgentccBatchJob.objects.create(
        organization=user.organization,
        gateway_batch_id="test-only-legacy",
        gateway_key_id="test-only-legacy-key",
        total=1,
    )
    pending, grant = batch(user, workspace, pending=True)
    accepted, accepted_grant = batch(user, workspace)
    output = StringIO()
    call_command("retire_org_keys_733", "--accept-open-batch-loss", stdout=output)
    legacy.refresh_from_db()
    pending.refresh_from_db()
    accepted.refresh_from_db()
    grant.refresh_from_db()
    accepted_grant.refresh_from_db()
    assert legacy.notified and legacy.status == "failed"
    assert '"lost_batches": 1' in output.getvalue()
    assert not pending.notified and not accepted.notified
    assert grant.revoked_at is None and accepted_grant.revoked_at is None
    call_command("retire_org_keys_733", stdout=StringIO())


def test_delegated_recorded_result_retains_revoked_grant_identity(user, workspace):
    job, grant = batch(user, workspace)
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["revoked_at"])
    job.status, job.notified, job.finished_at = "completed", True, timezone.now()
    job.save()
    result = recorded_result(job)
    assert result["key_id"] == "dlg_" + grant.jti
    serializer = GatewayBatchDetailResultSerializer(data=result)
    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    "service,mode,local,allowed",
    [
        ("bootstrap", "operator", "true", True),
        ("backend", "operator", "true", False),
        ("bootstrap", "disabled", "true", False),
        ("backend", "disabled", "true", False),
        ("backend", "disabled", "false", False),
    ],
)
def test_retirement_is_operator_only(monkeypatch, service, mode, local, allowed):
    from model_hub.apps import explicit_management_mutation_authorized

    monkeypatch.setenv("SERVICE_TYPE", service)
    monkeypatch.setenv("STARTUP_DB_MUTATION_MODE", mode)
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", local)
    monkeypatch.setenv("ENV_TYPE", "test")
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "")
    assert (
        explicit_management_mutation_authorized(["manage.py", "retire_org_keys_733"])
        is allowed
    )


@pytest.mark.parametrize(
    "options,allowed",
    [
        (["--dry-run"], True),
        (["--reverse", "--dry-run"], True),
        (["--reverse"], False),
        (["--dry-run", "--accept-open-batch-loss"], False),
    ],
)
def test_only_closed_readonly_preview_is_startup_safe(options, allowed):
    from model_hub.apps import guarded_management_command

    refused = guarded_management_command(["manage.py", "retire_org_keys_733", *options])
    assert (refused is None) is allowed
