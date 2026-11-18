"""UXF-10: the renewal preview names the release-rule changes since approval.

A packet stores only the rule counters it was approved under. The renewal
preview reports each change from the append-only audit rows (label protection
and quarantine events) with its before and after values, who changed it and
when. A counter bump that no audit row accounts for is reported with every
value null ("Values at approval weren't recorded"). A label pointer move is
not a rule change and adds no entry.
"""

from uuid import UUID

import pytest
from django.db.models import F

from model_hub.models.skills_hub import HubSkill
from model_hub.serializers import skills_hub_r3 as r3
from model_hub.services.skills_hub.labels import set_label
from model_hub.services.skills_hub.r3.release_requests import release_renewal_preview
from model_hub.services.skills_hub.v11.governance import set_label_protection
from model_hub.services.skills_hub.v12.safety import set_quarantine
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as fixtures

actors = fixtures.actors
candidate = fixtures.candidate
pytestmark = pytest.mark.django_db(transaction=True)


def _approved(actors, skill, version):
    author, reviewer = actors
    review = fixtures.preview(author, skill, version)
    request = fixtures.submit(author, reviewer, review)
    fixtures.approve(reviewer, request, review)
    return request


def _preview(reviewer, request):
    return release_renewal_preview(principal=reviewer, request_id=UUID(request["id"]))


def _without_times(changes):
    return [
        {key: value for key, value in row.items() if key != "changed_at"}
        for row in changes
    ]


def test_label_protection_change_reports_values_actor_and_time(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    request = _approved(actors, hub_skill, candidate[1])
    hub_skill.refresh_from_db()
    set_label_protection(
        principal=reviewer,
        skill_id=hub_skill.pk,
        protected_label_names=["staging", "production"],
        expected_release_revision=hub_skill.release_revision,
    )
    preview = _preview(reviewer, request)
    assert _without_times(preview["policy_changes"]) == [
        {
            "field": "protected_labels",
            "before": [],
            "after": ["production", "staging"],
            "changed_by": "Reviewer",
        }
    ]
    assert preview["policy_changes"][0]["changed_at"] is not None
    # The existing precondition rows are unchanged by the new field.
    assert {row["item"] for row in preview["changes"]} == {
        "transition.expected_release_revision"
    }


def test_quarantine_changes_report_each_state_in_order(actors, hub_skill, candidate):
    author, reviewer = actors
    request = _approved(actors, hub_skill, candidate[1])
    for revision, active in ((0, True), (1, False)):
        set_quarantine(
            principal=reviewer,
            version_id=candidate[0].pk,
            revision=revision,
            active=active,
            reason="Synthetic safety review",
            replacement_version_id=None,
        )
    preview = _preview(reviewer, request)
    number = candidate[0].number
    assert _without_times(preview["policy_changes"]) == [
        {
            "field": "quarantine",
            "before": {"version": number, "quarantined": False},
            "after": {"version": number, "quarantined": True},
            "changed_by": "Reviewer",
        },
        {
            "field": "quarantine",
            "before": {"version": number, "quarantined": True},
            "after": {"version": number, "quarantined": False},
            "changed_by": "Reviewer",
        },
    ]
    # A quarantine that was set and cleared still voids the approval.
    assert {row["item"] for row in preview["changes"]} == {
        "transition.expected_security_revision"
    }


def test_label_move_is_not_a_rule_change(actors, hub_skill, candidate, hub_actor):
    author, reviewer = actors
    request = _approved(actors, hub_skill, candidate[1])
    hub_skill.refresh_from_db()
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="staging",
        version_number=candidate[0].number,
    )
    preview = _preview(reviewer, request)
    assert preview["policy_changes"] == []
    assert {row["item"] for row in preview["changes"]} == {
        "transition.expected_release_revision"
    }


@pytest.mark.parametrize(
    ("counter", "field"),
    [("security_revision", "security_rules"), ("release_revision", "release_rules")],
)
def test_unrecorded_bump_returns_field_with_null_values(
    actors, hub_skill, candidate, counter, field
):
    author, reviewer = actors
    request = _approved(actors, hub_skill, candidate[1])
    HubSkill.objects.filter(pk=hub_skill.pk).update(**{counter: F(counter) + 1})
    preview = _preview(reviewer, request)
    assert preview["policy_changes"] == [
        {
            "field": field,
            "before": None,
            "after": None,
            "changed_by": None,
            "changed_at": None,
        }
    ]


def test_policy_change_serializer_shape(actors, hub_skill, candidate):
    author, reviewer = actors
    request = _approved(actors, hub_skill, candidate[1])
    hub_skill.refresh_from_db()
    set_label_protection(
        principal=reviewer,
        skill_id=hub_skill.pk,
        protected_label_names=["production"],
        expected_release_revision=hub_skill.release_revision,
    )
    HubSkill.objects.filter(pk=hub_skill.pk).update(
        security_revision=F("security_revision") + 1
    )
    preview = _preview(reviewer, request)
    data = r3.PolicyChange(preview["policy_changes"], many=True).data
    assert [row["field"] for row in data] == ["protected_labels", "security_rules"]
    assert data[0]["before"] == [] and data[0]["after"] == ["production"]
    assert isinstance(data[0]["changed_at"], str)
    assert data[1]["changed_at"] is None and data[1]["changed_by"] is None


def test_renewal_route_returns_policy_changes(
    actors, hub_skill, candidate, organization, workspace
):
    """The mounted route carries the rule values, not just the service result."""
    from uuid import uuid4

    from rest_framework.test import APIClient

    from accounts.models import OrgApiKey

    author, reviewer = actors
    request = _approved(actors, hub_skill, candidate[1])
    hub_skill.refresh_from_db()
    set_label_protection(
        principal=reviewer,
        skill_id=hub_skill.pk,
        protected_label_names=["staging", "production"],
        expected_release_revision=hub_skill.release_revision,
    )
    key = OrgApiKey.objects.create(
        name="Synthetic reviewer",
        type="user",
        user_id=reviewer.user_id,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    response = client.get(f"/model-hub/skills/release-requests/{request['id']}/renewal/")
    assert response.status_code == 200, response.data
    rows = response.data["result"]["policy_changes"]
    assert [row["field"] for row in rows] == ["protected_labels"]
    assert rows[0]["after"] == ["production", "staging"]
    assert rows[0]["changed_by"] and rows[0]["changed_at"]
