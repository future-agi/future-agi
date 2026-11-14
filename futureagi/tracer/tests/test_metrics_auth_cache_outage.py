"""Metrics authorization must survive only the optional token-cache outage."""

from unittest.mock import patch

import pytest
from django.utils import timezone
from redis.exceptions import TimeoutError
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models import User
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_annotations import AnnotationsLabels


@pytest.mark.django_db
@pytest.mark.parametrize("project_scoped", [True, False])
def test_bearer_metrics_retains_tenant_scope_during_token_cache_outage(
    user, workspace, observe_project, project_scoped
):
    project = observe_project
    own = AnnotationsLabels.objects.create(
        name="cache-outage-annotation",
        type="star",
        settings={"no_of_stars": 5},
        organization=user.organization,
        workspace=workspace,
        project=project,
    )
    other_org = Organization.objects.create(name="Other cache outage tenant")
    other_user = User.objects.create_user(
        email="cache-outage-other@futureagi.com",
        name="Other owner",
        organization=other_org,
    )
    other_workspace = Workspace.objects.create(
        name="Other workspace", organization=other_org, created_by=other_user
    )
    foreign = AnnotationsLabels.objects.create(
        name="cache-outage-annotation",
        type="star",
        settings={"no_of_stars": 5},
        organization=other_org,
        workspace=other_workspace,
    )
    row = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        last_used_at=timezone.now(),
    )
    token = generate_encrypted_message({"user_id": str(user.id), "id": str(row.id)})
    client = APIClient()  # No force_authenticate or workspace injection.
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
        HTTP_X_ORGANIZATION_ID=str(user.organization_id),
    )
    with patch("accounts.authentication.cache") as cache:
        cache.get.side_effect = TimeoutError("token cache unavailable")
        response = client.post(
            "/tracer/dashboard/metrics/",
            {
                "source": "traces",
                "category": "annotation_metric",
                "search": own.name,
                # Exercise the DB-backed definition catalog without a CH mirror.
                # ANNOT-E2E-004 separately covers the unified cursor-mode reader.
                **({"project_ids": str(project.id)} if project_scoped else {}),
                "page_size": 25,
            },
            format="json",
        )
    assert response.status_code == 200, response.json()
    metrics = response.json()["result"]["metrics"]
    assert [(entry["name"], entry["output_type"]) for entry in metrics] == [
        (str(own.id), "star")
    ]
    assert str(foreign.id) not in str(metrics)
    cache.set.assert_not_called()
