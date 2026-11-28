from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub import HubSkillLabel
from model_hub.models.skills_hub_v12 import HubSkillContextCredential
from model_hub.serializers.skills_hub_v12 import SessionOut
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.consumers import (
    append_receipt,
    check_session_access,
    open_context_session,
)

pytestmark = pytest.mark.django_db


def test_session_response_matches_foundation_and_accounts_success(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    HubSkillLabel.objects.create(skill=context, name="production", version=version)
    consume = credential(version)
    request = {
        "client_session_key": str(uuid4()),
        "incarnation_id": str(uuid4()),
        "resume_session_id": None,
        "adapter_version": "synthetic-adapter",
        "runtime_version": "synthetic-runtime",
        "runtime_config_digest": "sha256:" + "d" * 64,
    }
    row = HubSkillContextCredential.objects.get(pk=consume.credential_id)
    assert row.last_used_at is None
    with pytest.raises(SkillsHubError) as exc:
        open_context_session(
            principal=principal,
            binding_id=binding["id"],
            request=request,
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "credential_operation_forbidden"
    output = open_context_session(
        principal=consume,
        binding_id=binding["id"],
        request=request,
        idempotency_key=uuid4(),
    )
    schema = SessionOut(data=output)
    assert schema.is_valid(), schema.errors
    row = HubSkillContextCredential.objects.get(pk=row.pk)
    assert row.last_used_at is not None
    opened_at = row.last_used_at
    check_session_access(principal=consume, session_id=output["session_id"])
    row = HubSkillContextCredential.objects.get(pk=row.pk)
    assert row.last_used_at is not None
    assert row.last_used_at >= opened_at


def test_reporting_updates_last_use_only_after_acceptance(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    reporter = credential(version, purpose="report", binding_id=binding["id"])
    row = HubSkillContextCredential.objects.get(pk=reporter.credential_id)
    receipt = {
        "event_id": str(uuid4()),
        "session_id": None,
        "instance_id": str(uuid4()),
        "sequence": 0,
        "stage": "installed",
        "version_id": str(version.pk),
        "digest": version.digest,
        "materialization_digest": None,
        "observed_at": timezone.now(),
        "details": {
            "path": None,
            "sha256": None,
            "ranges": [],
            "runtime_version": None,
            "adapter_version": "synthetic-adapter",
            "diagnostic_codes": [],
            "trace_ref": None,
        },
    }
    with pytest.raises(SkillsHubError):
        append_receipt(
            principal=reporter,
            binding_id=binding["id"],
            receipt={**receipt, "digest": "sha256:" + "0" * 64},
        )
    row = HubSkillContextCredential.objects.get(pk=row.pk)
    assert row.last_used_at is None
    result = append_receipt(
        principal=reporter, binding_id=binding["id"], receipt=receipt
    )
    row = HubSkillContextCredential.objects.get(pk=row.pk)
    assert row.last_used_at is not None
    assert (
        append_receipt(principal=reporter, binding_id=binding["id"], receipt=receipt)
        == result
    )
