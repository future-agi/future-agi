"""UXF-07: one fetch read serves skills and agent contexts.

The shared `<id>/usage/` route names the resource kind instead of a context
gaining its own route. A context id is read only with kind=agent_context, in
the caller's workspace; the skill path keeps its selector and error contract.
A fetch is a read of the resource, never proof that an agent used it.
"""

from datetime import timedelta

import pytest
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkillUsageDaily
from model_hub.views.skills_hub import SkillUsageView

pytestmark = pytest.mark.django_db


def url(resource):
    return f"/model-hub/skills/{resource.pk}/usage/"


def get(user, organization, workspace, resource, **params):
    request = APIRequestFactory().get(url(resource), params)
    force_authenticate(request, user=user)
    request.organization = organization
    request.workspace = workspace
    match = resolve(url(resource))
    return match.func(request, **match.kwargs)


def bucket(resource, version, *, days_ago=0, count=1, channel="sdk"):
    now = timezone.now()
    return HubSkillUsageDaily.objects.create(
        skill=resource,
        version=version,
        day=(now - timedelta(days=days_ago)).date(),
        channel=channel,
        consumer_key=f"credential:{channel}-{days_ago}",
        consumer_label="",
        sdk_version="0.8.0",
        sdk_language="python",
        tracing_mode="off",
        count=count,
        last_at=now - timedelta(days=days_ago),
    )


@pytest.fixture
def context(hub_skill, publish_skill):
    version = publish_skill(hub_skill).version
    hub_skill.kind = "agent_context"
    hub_skill.save(update_fields=["kind"])
    return hub_skill, version


def test_mounted_on_the_shared_usage_route(hub_skill):
    assert resolve(url(hub_skill)).func.view_class is SkillUsageView


def test_context_fetches_are_read_with_its_kind(user, organization, workspace, context):
    resource, version = context
    bucket(resource, version, count=6)
    response = get(
        user, organization, workspace, resource, kind="agent_context", days=7
    )
    assert response.status_code == 200, response.data
    body = response.data["result"]
    assert body["total"] == 6
    assert body["by_channel"]["sdk"] == 6
    consumer = body["consumers"][0]
    assert (consumer["channel"], consumer["sdk_version"], consumer["tracing_mode"]) == (
        "sdk",
        "0.8.0",
        "off",
    )
    assert [(row["version"], row["count"]) for row in body["by_version"]] == [
        (version.number, 6)
    ]
    assert (
        body["consumers"][0]["can_record"] is False
    )  # tracing off: a fetch, not a load
    assert body["first_fetched_on"] == timezone.now().date().isoformat()


def test_first_fetch_is_all_time_not_the_window(user, organization, workspace, context):
    resource, version = context
    bucket(resource, version, days_ago=40)
    body = get(
        user, organization, workspace, resource, kind="agent_context", days=7
    ).data["result"]
    assert body["total"] == 0
    assert (
        body["first_fetched_on"]
        == (timezone.now() - timedelta(days=40)).date().isoformat()
    )


def test_never_fetched_context_has_no_first_fetch(
    user, organization, workspace, context
):
    resource, _ = context
    body = get(user, organization, workspace, resource, kind="agent_context").data[
        "result"
    ]
    assert (body["total"], body["first_fetched_on"], body["consumers"]) == (0, None, [])


def test_context_id_without_its_kind_keeps_the_skill_contract(
    user, organization, workspace, context
):
    resource, _ = context
    response = get(user, organization, workspace, resource)
    assert response.status_code == 404
    assert response.data["code"] == "skill_not_found"


def test_skill_id_is_not_read_as_a_context(
    user, organization, workspace, hub_skill, publish_skill
):
    publish_skill(hub_skill)
    response = get(user, organization, workspace, hub_skill, kind="agent_context")
    assert response.status_code == 404
    assert response.data["code"] == "resource_not_found"
    # The skill itself still reads through the default kind.
    assert get(user, organization, workspace, hub_skill).status_code == 200


def test_other_workspace_context_is_not_visible(user, organization, workspace, context):
    resource, version = context
    bucket(resource, version)
    other = Workspace.objects.create(
        organization=organization, name="Other", created_by=user
    )
    response = get(user, organization, other, resource, kind="agent_context")
    assert response.status_code in (403, 404), response.data
    assert "total" not in str(response.data)


def test_unknown_kind_is_refused(user, organization, workspace, context):
    resource, _ = context
    assert get(user, organization, workspace, resource, kind="tool").status_code == 400
