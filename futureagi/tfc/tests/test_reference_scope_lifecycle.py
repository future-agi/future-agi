"""Reference controls retain normal invitation and serializer lifecycles."""

import uuid

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from model_hub.models.evals_metric import EvalTemplate
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant
from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer
from tracer.tests.test_relation_request_scope import request_for, resources
from tracer.tests.test_relation_request_scope import scopes as relation_scopes

pytestmark = pytest.mark.django_db


@pytest.fixture
def scopes(settings):
    return relation_scopes.__wrapped__(settings)


def test_shared_template_control(scopes):
    a = scopes[0]
    clear_workspace_context()
    template = EvalTemplate.no_workspace_objects.create(
        organization=None,
        workspace=None,
        name=f"shared-{uuid.uuid4()}",
        config={"required_keys": []},
    )
    field = CustomEvalConfigSerializer(context={"request": request_for(a)}).fields[
        "eval_template"
    ]
    assert field.run_validation(str(template.pk)) == template


@pytest.mark.parametrize("other_index", [1, 2])
def test_config_update_and_list_require_project_scope(scopes, other_index):
    a = scopes[0]
    other = resources(scopes[other_index])["custom_eval_config"]
    clear_workspace_context()
    response = a.client.patch(
        f"/tracer/custom-eval-config/{other.pk}/", {"name": "Changed"}, format="json"
    )
    assert response.status_code in (400, 403, 404), response.content
    other.refresh_from_db()
    assert other.name == "Config"
    clear_workspace_context()
    response = a.client.get(
        "/tracer/custom-eval-config/", {"project_id": str(other.project_id)}
    )
    assert str(other.pk) not in response.content.decode()


def test_pending_invitation_can_still_be_managed(settings):
    settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
    a = make_tenant("A")
    clear_workspace_context()
    pending = User.objects.create_user(
        email=f"pending-{uuid.uuid4().hex}@example.com",
        name="Pending",
        organization=a.organization,
        invited_by=a.user,
        is_active=False,
        password="testpassword123",
    )
    OrganizationMembership.no_workspace_objects.create(
        organization=a.organization,
        user=pending,
        invited_by=a.user,
        is_active=False,
        role="Member",
        level=Level.MEMBER,
    )
    clear_workspace_context()
    renamed = a.client.post(
        "/accounts/update-user/",
        {"user_id": str(pending.pk), "name": "Invited"},
        format="json",
    )
    assert renamed.status_code == 200, renamed.content
    pending.refresh_from_db()
    assert pending.name == "Invited"
    clear_workspace_context()
    deleted = a.client.delete(
        "/accounts/delete-users/", {"user_ids": [str(pending.pk)]}, format="json"
    )
    assert deleted.status_code == 200, deleted.content
    assert not User.objects.filter(pk=pending.pk).exists()
