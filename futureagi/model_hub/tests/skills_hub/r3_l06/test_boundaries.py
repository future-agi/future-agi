from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from model_hub.models.skills_hub import HubSkillLabel
from model_hub.models.skills_hub_v12 import (
    HubSkillContextCredential,
    HubSkillContextSession,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.consumers import (
    check_session_access,
    list_bindings,
    open_context_session,
)

pytestmark = pytest.mark.django_db


def request():
    return {
        "client_session_key": str(uuid4()),
        "incarnation_id": str(uuid4()),
        "resume_session_id": None,
        "adapter_version": "synthetic-v1",
        "runtime_version": "synthetic-runtime",
        "runtime_config_digest": "sha256:" + "c" * 64,
    }


def test_missing_label_never_falls_back_to_latest(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    principal = credential(version)
    with pytest.raises(SkillsHubError) as exc:
        open_context_session(
            principal=principal,
            binding_id=binding["id"],
            request=request(),
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "label_not_found"
    assert not HubSkillContextSession.objects.exists()


def test_missing_or_expired_mapping_requires_clean_thread(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    HubSkillLabel.objects.create(skill=context, name="production", version=version)
    principal = credential(version)
    body = request()
    opened = open_context_session(
        principal=principal,
        binding_id=binding["id"],
        request=body,
        idempotency_key=uuid4(),
    )
    HubSkillContextSession.objects.filter(pk=opened["session_id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(SkillsHubError) as exc:
        open_context_session(
            principal=principal,
            binding_id=binding["id"],
            request={**body, "resume_session_id": opened["session_id"]},
            idempotency_key=uuid4(),
        )
    assert exc.value.code == "thread_context_unknown"
    assert HubSkillContextSession.objects.count() == 1


def test_other_credential_cannot_reuse_session(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    HubSkillLabel.objects.create(skill=context, name="production", version=version)
    first, second = credential(version), credential(version)
    opened = open_context_session(
        principal=first,
        binding_id=binding["id"],
        request=request(),
        idempotency_key=uuid4(),
    )
    with pytest.raises(SkillsHubError) as exc:
        check_session_access(principal=second, session_id=opened["session_id"])
    assert exc.value.code == "resource_not_found"
    HubSkillContextCredential.objects.filter(pk=first.credential_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(SkillsHubError) as exc:
        check_session_access(principal=first, session_id=opened["session_id"])
    assert exc.value.code == "context_credential_expired"


def test_lost_membership_removes_binding_count(principal, context, binding):
    assert list_bindings(principal=principal, context_id=context.pk)["count"] == 1
    OrganizationMembership.no_workspace_objects.filter(
        user_id=principal.user_id, organization_id=principal.scope.organization_id
    ).update(is_active=False)
    with pytest.raises(SkillsHubError) as exc:
        list_bindings(principal=principal, context_id=context.pk)
    assert exc.value.code == "access_revoked"


@pytest.mark.django_db(transaction=True)
def test_simultaneous_session_retries_allocate_once(
    principal, context, context_version, binding, credential
):
    version = context_version(context)
    HubSkillLabel.objects.create(skill=context, name="production", version=version)
    principal = credential(version)
    body, key = request(), uuid4()

    def open_once():
        close_old_connections()
        try:
            return open_context_session(
                principal=principal,
                binding_id=binding["id"],
                request=body,
                idempotency_key=key,
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(open_once) for _ in range(2)]
        outputs = [future.result(timeout=20) for future in futures]
    assert outputs[0] == outputs[1]
    assert HubSkillContextSession.objects.filter(binding_id=binding["id"]).count() == 1
