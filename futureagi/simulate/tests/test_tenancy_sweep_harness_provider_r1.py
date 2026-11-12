"""R-12: session/service fallback, without force_authenticate.

This is deliberately NOT a routed APIKeyAuthentication request: the row is the
helper path authentication did not bind. A Django RequestFactory request models
an already-session-authenticated user with no request.workspace. The real
can_access_workspace method is retained. At IR it already checks live org
membership, so these are predicted PASS, not manufactured REDs.
"""

from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from simulate.models.hosted_harness import HostedHarnessJob
from simulate.services.harness_provider import scope_jobs
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def harness_scope(user, organization, workspace):
    clear_workspace_context()
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER, is_active=True)
    user.organization_role = OrganizationRoles.MEMBER
    user.save(update_fields=["organization_role"])
    other = Workspace.no_workspace_objects.create(
        organization=organization,
        name=f"r12-w2-{uuid4().hex}",
        is_active=True,
        created_by=user,
    )
    foreign_org = Organization.objects.create(name=f"r12-org-b-{uuid4().hex}")
    foreign = Workspace.no_workspace_objects.create(
        organization=foreign_org,
        name=f"r12-b-w1-{uuid4().hex}",
        is_active=True,
        created_by=user,
    )
    # created_by is a required creator, not access: drop any membership a
    # creation signal may have granted so the actor stays outside both.
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace__in=[other, foreign]
    ).delete()
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=foreign_org
    ).delete()
    jobs = {}
    for name, space in (
        ("own", workspace),
        ("workspace", other),
        ("organization", foreign),
    ):
        run_id = uuid4()
        jobs[name] = HostedHarnessJob.no_workspace_objects.create(
            organization=space.organization,
            workspace=space,
            run_id=run_id,
            idempotency_key=f"r12-{run_id}",
            request_digest="sha256:" + sha256(str(run_id).encode()).hexdigest(),
            schema_version="v1.6",
            payload={"synthetic": True},
            seed=1,
            scenario_count=1,
            artifact_level="none",
            max_artifact_bytes=0,
            deadline_at=timezone.now() + timedelta(minutes=5),
        )
    yield SimpleNamespace(
        user=user,
        organization=organization,
        workspace=workspace,
        other=other,
        foreign=foreign,
        jobs=jobs,
    )
    clear_workspace_context()


def session_request(scope, workspace_id):
    # No DRF force-auth helper, and no patched membership/access decision.
    request = RequestFactory().get(
        "/service-boundary/scope-jobs/", HTTP_X_WORKSPACE_ID=str(workspace_id)
    )
    request.user = User.objects.get(pk=scope.user.pk)
    request.workspace = None
    assert request.user.is_authenticated
    assert request.user.organization_id == scope.organization.pk
    return request


@pytest.mark.parametrize("denial", ["inactive-membership", "workspace", "organization"])
def test_r12_fallback_rejects_unauthorized_workspace(harness_scope, denial):
    scope = harness_scope
    target = scope.workspace
    if denial == "inactive-membership":
        OrganizationMembership.no_workspace_objects.filter(
            user=scope.user, organization=scope.organization
        ).update(is_active=False)
        # Keep the workspace membership active: only the real organization
        # membership guard can make this request safe.
        assert WorkspaceMembership.no_workspace_objects.filter(
            user=scope.user, workspace=target, is_active=True
        ).exists()
    else:
        target = scope.other if denial == "workspace" else scope.foreign
    request = session_request(scope, target.pk)
    delivered = []
    with pytest.raises(PermissionDenied) as refused:
        delivered.extend(
            scope_jobs(
                HostedHarnessJob.no_workspace_objects.filter(
                    organization=scope.organization
                ),
                request,
            )
        )
    assert refused.value.status_code == 403
    assert delivered == []
    # Refusal is a read-only operation, not deletion of the foreign fixtures.
    assert (
        HostedHarnessJob.no_workspace_objects.filter(
            pk__in=[job.pk for job in scope.jobs.values()]
        ).count()
        == 3
    )


def test_r12_active_same_tenant_session_gets_only_own_job(harness_scope):
    scope = harness_scope
    request = session_request(scope, scope.workspace.pk)
    result = list(
        scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(
                organization=scope.organization
            ),
            request,
        ).values_list("pk", flat=True)
    )
    assert result == [scope.jobs["own"].pk]
    assert scope.jobs["workspace"].pk not in result
    assert scope.jobs["organization"].pk not in result
