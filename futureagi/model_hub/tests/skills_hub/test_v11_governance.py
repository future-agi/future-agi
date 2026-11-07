"""Release guards use actual scoped memberships; shared v1 wiring is separate."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import close_old_connections, transaction

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.skills_hub import HubSkill, HubSkillLabel
from model_hub.models.skills_hub_v11 import HubSkillLabelPolicyEvent
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.types import MISSING, Actor
from model_hub.services.skills_hub.v11.governance import (
    _validated_names,
    advance_release_revision_locked,
    authorize_archive_locked,
    authorize_label_change_locked,
    authorize_release_locked,
    principal_for_actor,
    set_label_protection,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope

pytestmark = pytest.mark.django_db
HAS_RELEASE_FIELDS = any(
    field.name == "release_revision" for field in HubSkill._meta.fields
)
needs_shared_fields = pytest.mark.skipif(
    not HAS_RELEASE_FIELDS,
    reason="Integration-owned HubSkill release fields not installed yet",
)


@pytest.fixture
def principal(organization, workspace, user):
    return SkillPrincipal(
        SkillScope(organization.id, workspace.id), user.id, None, "human"
    )


@pytest.fixture
def protected(hub_skill):
    # Guard-only tests work before the integration owner's v1 field additions.
    hub_skill.protected_label_names = ["production"]
    hub_skill.release_revision = 4
    return hub_skill


def change(principal, skill, **kwargs):
    args = {
        "principal": principal,
        "skill": skill,
        "name": "production",
        "expected_version": None,
        "expected_release_revision": 4,
    }
    args.update(kwargs)
    with transaction.atomic():
        authorize_label_change_locked(**args)


def roles(user, organization, workspace, *, org=3, ws=3):
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(level=org)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=ws)


def test_admin_first_assignment_and_explicit_null(principal, protected):
    change(principal, protected)
    for kwargs in (
        {"expected_release_revision": MISSING},
        {"expected_version": MISSING},
    ):
        with pytest.raises(SkillsHubError) as error:
            change(principal, protected, **kwargs)
        assert (
            error.value.code == "release_precondition_required"
            and error.value.status == 428
        )


def test_member_can_publish_without_protected_move_but_not_noop(
    principal, protected, user, organization, workspace
):
    roles(user, organization, workspace)
    with transaction.atomic():
        authorize_release_locked(principal=principal, skill=protected, label_names=[])
        authorize_release_locked(
            principal=principal, skill=protected, label_names=["staging"]
        )
    with pytest.raises(SkillsHubError) as error:
        change(principal, protected)
    assert error.value.code == "protected_label_forbidden"


@pytest.mark.parametrize("org,ws", [(8, 1), (15, 1), (3, 8)])
def test_current_target_workspace_or_org_admin(
    principal, protected, user, organization, workspace, org, ws
):
    roles(user, organization, workspace, org=org, ws=ws)
    change(principal, protected)


def test_other_workspace_admin_and_inactive_membership_do_not_authorize(
    principal, protected, user, organization, workspace, hub_other_workspace
):
    roles(user, organization, workspace)
    membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=organization
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=hub_other_workspace,
        organization_membership=membership,
        level=8,
        is_active=True,
    )
    with pytest.raises(SkillsHubError):
        change(principal, protected)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=8, is_active=False)
    with pytest.raises(SkillsHubError):
        change(principal, protected)
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    with pytest.raises(SkillsHubError):
        change(principal, protected)


def test_foreign_scope_and_archived_skill(principal, protected):
    for scope in (
        SkillScope(uuid4(), principal.scope.workspace_id),
        SkillScope(principal.scope.organization_id, uuid4()),
    ):
        with pytest.raises(SkillsHubError) as error:
            change(replace(principal, scope=scope), protected)
        assert error.value.status == 404
    protected.deleted = True
    with pytest.raises(SkillsHubError) as error:
        change(principal, protected)
    assert error.value.status == 404


def test_system_key_never_borrows_synthetic_admin(
    principal, protected, user, organization, workspace
):
    key = OrgApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        type="system",
        user=user,
        api_key=f"synthetic-{uuid4()}",
        secret_key="synthetic-unused",
    )
    actor = Actor(user=user, api_key=key)
    system = principal_for_actor(skill=protected, actor=actor)
    assert system.user_id is None and system.kind == "system_key"
    with transaction.atomic():
        authorize_release_locked(
            principal=system, skill=protected, label_names=["staging"]
        )
    with pytest.raises(SkillsHubError) as error:
        change(system, protected)
    assert error.value.code == "principal_not_supported"
    with pytest.raises(SkillsHubError) as error:
        set_label_protection(
            principal=system,
            skill_id=uuid4(),
            protected_label_names=[],
            expected_release_revision=4,
        )
    assert error.value.code == "principal_not_supported"


@pytest.mark.parametrize("kind", ["user", "mcp"])
def test_bound_key_records_identity_and_revocation(
    principal, protected, user, organization, workspace, kind
):
    key = OrgApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        type=kind,
        user=user,
        api_key=f"synthetic-{uuid4()}",
        secret_key="synthetic-unused",
    )
    delegated = principal_for_actor(
        skill=protected, actor=Actor(user=user, api_key=key)
    )
    assert delegated.user_id == user.id and delegated.api_key_id == key.id
    change(delegated, protected)
    key.enabled = False
    key.save(update_fields=["enabled"])
    with pytest.raises(SkillsHubError):
        change(delegated, protected)


def test_noop_pointer_and_aba_still_check_aggregate_revision(
    principal, protected, publish_skill
):
    version = publish_skill(protected).version
    HubSkillLabel.objects.create(skill=protected, name="production", version=version)
    change(principal, protected, expected_version=1)
    # A -> B -> A has the same pointer but a newer aggregate revision.
    protected.release_revision = 6
    with pytest.raises(SkillsHubError) as error:
        change(principal, protected, expected_version=1)
    assert (
        error.value.code == "release_conflict"
        and error.value.detail["release_revision"] == 6
    )
    with pytest.raises(SkillsHubError) as error:
        change(principal, protected, expected_release_revision=6, expected_version=None)
    assert error.value.code == "release_conflict"


def test_unprotected_pointer_preserves_legacy_optional_cas(
    principal, protected, publish_skill
):
    version = publish_skill(protected).version
    HubSkillLabel.objects.create(skill=protected, name="staging", version=version)
    change(
        principal,
        protected,
        name="staging",
        expected_version=MISSING,
        expected_release_revision=MISSING,
    )
    with pytest.raises(SkillsHubError) as error:
        change(principal, protected, name="staging", expected_version=None)
    assert error.value.code == "label_conflict"


def test_archive_requires_admin_only_when_protected_pointer_assigned(
    principal, protected, publish_skill, user, organization, workspace
):
    version = publish_skill(protected).version
    roles(user, organization, workspace)
    with transaction.atomic():
        authorize_archive_locked(principal=principal, skill=protected)
        HubSkillLabel.objects.create(
            skill=protected, name="production", version=version
        )
        with pytest.raises(SkillsHubError) as error:
            authorize_archive_locked(
                principal=principal, skill=protected, expected_release_revision=4
            )
        assert error.value.code == "protected_label_forbidden"


@pytest.mark.parametrize(
    "names",
    [
        ["latest"],
        ["Production"],
        ["a", "a"],
        ["../a"],
        ["a"] * 21,
        [None],
        "production",
    ],
)
def test_policy_names_are_bounded_unique_and_valid(names):
    with pytest.raises(SkillsHubError) as error:
        _validated_names(names)
    assert error.value.code == "invalid_label"


def test_policy_canonical_order_and_empty():
    assert _validated_names(["staging", "production"]) == ["production", "staging"]
    assert _validated_names([]) == []


@pytest.mark.parametrize("revision", [None, True, -1, "4"])
def test_revision_types_are_not_coerced(principal, protected, revision):
    with pytest.raises(SkillsHubError) as error:
        change(principal, protected, expected_release_revision=revision)
    assert error.value.status == 400


@needs_shared_fields
def test_policy_change_audit_noop_and_stale_cas(principal, hub_skill):
    result = set_label_protection(
        principal=principal,
        skill_id=hub_skill.id,
        protected_label_names=["staging"],
        expected_release_revision=0,
    )
    assert result == {
        "protected_label_names": ["staging"],
        "release_revision": 1,
        "can_manage": True,
    }
    event = HubSkillLabelPolicyEvent.objects.get(skill=hub_skill)
    assert (
        event.actor_id == principal.user_id
        and event.from_revision == 0
        and event.to_revision == 1
    )
    same = set_label_protection(
        principal=principal,
        skill_id=hub_skill.id,
        protected_label_names=["staging"],
        expected_release_revision=1,
    )
    assert (
        same["release_revision"] == 1
        and HubSkillLabelPolicyEvent.objects.filter(skill=hub_skill).count() == 1
    )
    with pytest.raises(SkillsHubError) as error:
        set_label_protection(
            principal=principal,
            skill_id=hub_skill.id,
            protected_label_names=["staging"],
            expected_release_revision=0,
        )
    assert error.value.code == "release_conflict"


@needs_shared_fields
def test_policy_event_failure_rolls_back_policy_and_revision(principal, hub_skill):
    original = list(hub_skill.protected_label_names)
    with patch.object(
        HubSkillLabelPolicyEvent.objects,
        "create",
        side_effect=RuntimeError("synthetic audit failure"),
    ):
        with pytest.raises(RuntimeError):
            set_label_protection(
                principal=principal,
                skill_id=hub_skill.id,
                protected_label_names=["production"],
                expected_release_revision=0,
            )
    hub_skill.refresh_from_db()
    assert (
        hub_skill.protected_label_names == original and hub_skill.release_revision == 0
    )


@needs_shared_fields
def test_denied_bundled_publication_rolls_back_version_and_preserves_draft(
    principal, hub_skill, user, organization, workspace
):
    from model_hub.services.skills_hub.drafts import publish_draft

    hub_skill.protected_label_names = ["production"]
    hub_skill.save(update_fields=["protected_label_names"])
    before = hub_skill.drafts.get(user=user)
    roles(user, organization, workspace)
    with pytest.raises(SkillsHubError) as error:
        publish_draft(
            skill=hub_skill,
            user=user,
            actor=Actor(user=user, api_key=None),
            labels=["production"],
            acknowledge_warnings=True,
            force=True,
            revision=before.revision,
            expected_release_revision=0,
        )
    assert error.value.code == "protected_label_forbidden"
    assert hub_skill.versions.count() == 0
    assert hub_skill.drafts.get(user=user).revision == before.revision


@needs_shared_fields
def test_integrated_label_aba_noop_and_removal(
    principal, hub_skill, hub_actor, publish_skill, make_skill_files
):
    from model_hub.services.skills_hub.labels import remove_label, set_label

    hub_skill.protected_label_names = ["production"]
    hub_skill.save(update_fields=["protected_label_names"])
    publish_skill(hub_skill)
    publish_skill(hub_skill, files=make_skill_files(body="Second immutable version."))
    for target, expected, revision in [(1, None, 0), (2, 1, 1), (1, 2, 2)]:
        set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=target,
            expected_version=expected,
            expected_release_revision=revision,
        )
    with pytest.raises(SkillsHubError) as error:
        set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=1,
            expected_version=1,
            expected_release_revision=1,
        )
    assert error.value.code == "release_conflict"
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=1,
        expected_version=1,
        expected_release_revision=3,
    )
    hub_skill.refresh_from_db()
    assert hub_skill.release_revision == 3 and hub_skill.label_events.count() == 3
    remove_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        expected_version=1,
        expected_release_revision=3,
    )
    hub_skill.refresh_from_db()
    assert (
        hub_skill.release_revision == 4
        and "production" in hub_skill.protected_label_names
    )
    with pytest.raises(SkillsHubError) as error:
        set_label(skill=hub_skill, actor=hub_actor, name="production", version_number=1)
    assert error.value.code == "release_precondition_required"


@needs_shared_fields
@pytest.mark.django_db(transaction=True)
def test_concurrent_policy_cas_has_exactly_one_winner(principal, hub_skill):
    barrier = Barrier(2)

    def write(names):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            set_label_protection(
                principal=principal,
                skill_id=hub_skill.id,
                protected_label_names=names,
                expected_release_revision=0,
            )
            return "accepted"
        except SkillsHubError as error:
            return error.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [["staging"], ["review"]]))
    assert sorted(results) == ["accepted", "release_conflict"]
    assert HubSkillLabelPolicyEvent.objects.filter(skill=hub_skill).count() == 1
    hub_skill.refresh_from_db()
    assert hub_skill.release_revision == 1


def test_revision_advance_only_updates_revision_column():
    skill = HubSkill(release_revision=4)
    with patch.object(HubSkill, "save") as saved, transaction.atomic():
        assert advance_release_revision_locked(skill=skill) == 5
    saved.assert_called_once_with(update_fields=["release_revision"])
