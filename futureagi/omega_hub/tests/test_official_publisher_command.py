"""Production-safe Official bootstrap: only one reserved publisher, never users or keys."""

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import OrgApiKey
from omega_hub.models import AuditEvent, HubToken, Publisher
from omega_hub.services import audit
from omega_hub.tests.helpers import make_person

pytestmark = pytest.mark.django_db
COMMAND = "omega_hub_ensure_official_publisher"
ACTION = "publisher.official_ensured"


@pytest.fixture
def staff_owner(settings):
    settings.OMEGA_HUB_MAINTAINER_ORG_ID = ""
    person = make_person("official-operator")
    person.user.is_staff = True
    person.user.save(update_fields=["is_staff"])
    return person


def ensure(email):
    output = io.StringIO()
    call_command(COMMAND, "--owner-email", email, stdout=output)
    return output.getvalue()


def account_counts():
    return tuple(
        model.objects.count()
        for model in (get_user_model(), Organization, OrgApiKey, HubToken)
    )


@pytest.mark.parametrize("environment", ["production", "self-hosted"])
def test_create_and_repeat_are_audited_without_creating_users_orgs_or_keys(
    staff_owner, settings, environment
):
    settings.ENV_TYPE = environment
    settings.DEBUG = False
    settings.OMEGA_HUB_MAINTAINER_ORG_ID = str(staff_owner.organization.pk)
    before = account_counts()
    other = Publisher.objects.create(
        handle="unrelated",
        kind="user",
        owner_user=staff_owner.user,
        display_name="Keep me",
        tier=Publisher.Tier.COMMUNITY,
    )
    other_state = Publisher.objects.filter(pk=other.pk).values().get()
    ensure(staff_owner.email)
    official = Publisher.objects.get(handle="future-agi")
    assert official.kind == Publisher.Kind.ORGANIZATION
    assert official.tier == Publisher.Tier.OFFICIAL
    assert (
        official.owner_org_id == staff_owner.organization.pk
        and official.owner_user_id is None
    )
    assert official.display_name == "Future AGI"
    original_state = Publisher.objects.filter(pk=official.pk).values().get()
    ensure(staff_owner.email)
    assert Publisher.objects.filter(pk=official.pk).values().get() == original_state
    assert Publisher.objects.count() == 2
    assert Publisher.objects.filter(pk=other.pk).values().get() == other_state
    assert account_counts() == before
    events = list(AuditEvent.objects.filter(action=ACTION).order_by("at"))
    assert len(events) == 2
    assert [event.data["created"] for event in events] == [True, False]
    assert events[1].data["changedFields"] == []
    for event in events:
        assert event.actor_id == staff_owner.user.pk
        assert event.publisher_id == official.pk and event.target_id == str(official.pk)
        assert event.data["ownerOrgId"] == str(staff_owner.organization.pk)
        assert staff_owner.email not in str(event.data)


def test_repairs_tier_and_kind_without_overwriting_profile_or_other_publishers(
    staff_owner,
):
    official = Publisher.objects.create(
        handle="future-agi",
        kind="user",
        owner_user=staff_owner.user,
        display_name="Our Official team",
        bio="Keep this biography",
    )
    ensure(staff_owner.email)
    official.refresh_from_db()
    assert (
        official.kind == Publisher.Kind.ORGANIZATION
        and official.tier == Publisher.Tier.OFFICIAL
    )
    assert (
        official.owner_org_id == staff_owner.organization.pk
        and official.owner_user_id is None
    )
    assert (
        official.display_name == "Our Official team"
        and official.bio == "Keep this biography"
    )
    event = AuditEvent.objects.get(action=ACTION)
    assert set(event.data["changedFields"]) == {
        "kind",
        "tier",
        "owner_org_id",
        "owner_user_id",
    }


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "nonstaff",
        "inactive",
        "no-org",
        "wrong-maintainer-org",
    ],
)
def test_invalid_owner_is_refused_without_changes(staff_owner, settings, invalid):
    email = staff_owner.email
    if invalid == "missing":
        email = "absent@example.test"
    elif invalid == "nonstaff":
        get_user_model().objects.filter(pk=staff_owner.user.pk).update(is_staff=False)
    elif invalid == "inactive":
        get_user_model().objects.filter(pk=staff_owner.user.pk).update(is_active=False)
    elif invalid == "no-org":
        get_user_model().objects.filter(pk=staff_owner.user.pk).update(
            organization=None
        )
    else:
        settings.OMEGA_HUB_MAINTAINER_ORG_ID = "a-different-organization"
    before = account_counts()
    with pytest.raises(CommandError):
        ensure(email)
    assert (
        not Publisher.objects.exists()
        and not AuditEvent.objects.filter(action=ACTION).exists()
    )
    assert account_counts() == before


@pytest.mark.parametrize(
    "state", ["other-org", "other-user", "erased", "suspended", "deleted"]
)
def test_bootstrap_cannot_transfer_or_restore_an_existing_publisher(staff_owner, state):
    other = make_person("other-owner")
    fields = {"kind": "organization", "owner_org": staff_owner.organization}
    if state == "other-org":
        fields["owner_org"] = other.organization
    elif state == "other-user":
        fields = {"kind": "user", "owner_user": other.user}
    elif state == "erased":
        fields = {"kind": "organization", "erased_at": timezone.now()}
    elif state == "deleted":
        fields["deleted"] = True
    else:
        fields["suspended_at"] = timezone.now()
    official = Publisher.objects.create(
        handle="future-agi", display_name="Existing", **fields
    )
    before = Publisher.all_objects.filter(pk=official.pk).values().get()
    with pytest.raises(CommandError):
        ensure(staff_owner.email)
    assert Publisher.all_objects.filter(pk=official.pk).values().get() == before
    assert not AuditEvent.objects.filter(action=ACTION).exists()


def test_audit_failure_rolls_back_the_bootstrap(staff_owner, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        ensure(staff_owner.email)
    assert not Publisher.objects.exists()
