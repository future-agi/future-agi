"""Release checks shared by every publishing channel, under the skill row lock.

The v1 label/version services own pointer mutations. Call these guards before
allocating a version or taking a no-op path; advance the revision only for an
actual pointer change and store its return value on the label event.
"""

from __future__ import annotations

import re
from uuid import UUID

from django.db import connection, transaction

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v11 import HubSkillLabelPolicyEvent
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import LABEL_RE, MAX_LABELS, RESERVED_LABELS
from model_hub.services.skills_hub.types import MISSING, Actor
from model_hub.services.skills_hub.v11.principals import (
    require_scope_access,
    require_skill_admin,
)
from model_hub.services.skills_hub.v11.types import (
    PrincipalKind,
    SkillPrincipal,
    SkillScope,
)


def principal_for_actor(*, skill: HubSkill, actor: Actor) -> SkillPrincipal:
    """Bridge v1's actor without borrowing the authenticator's synthetic user."""
    key = actor.api_key
    kind: PrincipalKind
    if key is None:
        kind, user_id = "human", actor.user.pk if actor.user else None
    elif key.type in ("user", "mcp"):
        kind = "mcp_key" if key.type == "mcp" else "user_key"
        user_id = key.user_id
        if actor.user is None or actor.user.pk != user_id:
            raise SkillsHubError(
                "principal_not_supported",
                "The key must belong to this user.",
                status=403,
            )
    elif key.type == "system":
        kind, user_id = "system_key", None
    else:
        raise SkillsHubError(
            "principal_not_supported",
            "This key cannot perform skill releases.",
            status=403,
        )
    return SkillPrincipal(
        scope=SkillScope(skill.organization_id, skill.workspace_id),
        user_id=user_id,
        api_key_id=key.pk if key else None,
        kind=kind,
    )


def _require_locked_scope(*, principal: SkillPrincipal, skill: HubSkill) -> None:
    if not connection.in_atomic_block:
        raise RuntimeError(
            "Release checks require a transaction and the skill row lock."
        )
    if (
        principal.scope.organization_id != skill.organization_id
        or principal.scope.workspace_id != skill.workspace_id
        or skill.deleted
    ):
        raise SkillsHubError("resource_not_found", "Skill not found.", status=404)
    require_scope_access(principal=principal, write=True)


def _require_admin(*, principal: SkillPrincipal, skill: HubSkill, code: str) -> None:
    # A system key never inherits its synthetic user's release privileges.
    if principal.kind == "system_key":
        raise SkillsHubError(
            "principal_not_supported",
            "System keys cannot perform protected operations.",
            status=403,
        )
    try:
        require_skill_admin(principal=principal, skill=skill)
    except SkillsHubError as exc:
        if exc.status != 403 or exc.code == "principal_not_supported":
            raise
        raise SkillsHubError(
            code,
            "Only a workspace admin or organization admin/owner can change protection or protected releases.",
            status=403,
        ) from exc


def _check_revision(*, skill: HubSkill, expected: object, required: bool) -> None:
    if expected is MISSING:
        if required:
            raise SkillsHubError(
                "release_precondition_required",
                "Read the current release revision before changing this release.",
                status=428,
            )
        return
    if type(expected) is not int or expected < 0:
        raise SkillsHubError(
            "invalid_selector", "Use a nonnegative release revision.", status=400
        )
    if expected != skill.release_revision:
        raise SkillsHubError(
            "release_conflict",
            "The release or protection policy changed. Reload it before trying again.",
            status=409,
            detail={"release_revision": skill.release_revision},
        )


def authorize_release_locked(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    label_names: list[str],
    expected_release_revision: object = MISSING,
) -> None:
    """Preauthorize a whole publication once, including unchanged publications."""
    _require_locked_scope(principal=principal, skill=skill)
    protected = bool(set(label_names).intersection(skill.protected_label_names))
    if protected:
        _require_admin(
            principal=principal, skill=skill, code="protected_label_forbidden"
        )
    _check_revision(skill=skill, expected=expected_release_revision, required=protected)


def authorize_label_change_locked(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    name: str,
    expected_version: object = MISSING,
    expected_release_revision: object = MISSING,
) -> None:
    """Standalone PUT/DELETE requires both protected-release preconditions."""
    authorize_release_locked(
        principal=principal,
        skill=skill,
        label_names=[name],
        expected_release_revision=expected_release_revision,
    )
    protected = name in skill.protected_label_names
    if expected_version is MISSING:
        if protected:
            raise SkillsHubError(
                "release_precondition_required",
                "Supply the current label version, or null for first assignment.",
                status=428,
            )
        return
    if expected_version is not None and (
        type(expected_version) is not int or expected_version < 1
    ):
        raise SkillsHubError(
            "invalid_selector", "Use a positive version or null.", status=400
        )
    current = (
        skill.labels.filter(name=name).values_list("version__number", flat=True).first()
    )
    if current != expected_version:
        raise SkillsHubError(
            "release_conflict" if protected else "label_conflict",
            "This label changed. Reload it before trying again.",
            status=409,
            detail={
                "current_version": current,
                **({"release_revision": skill.release_revision} if protected else {}),
            },
        )


def authorize_archive_locked(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    expected_release_revision: object = MISSING,
) -> None:
    """Archiving an assigned protected pointer is a protected release operation."""
    _require_locked_scope(principal=principal, skill=skill)
    assigned = list(
        skill.labels.filter(name__in=skill.protected_label_names).values_list(
            "name", flat=True
        )
    )
    authorize_release_locked(
        principal=principal,
        skill=skill,
        label_names=assigned,
        expected_release_revision=expected_release_revision,
    )


def advance_release_revision_locked(*, skill: HubSkill) -> int:
    """Advance once per actual pointer/policy change while holding the skill lock."""
    if not connection.in_atomic_block:
        raise RuntimeError("Advancing a release requires the skill row lock.")
    skill.release_revision += 1
    skill.save(update_fields=["release_revision"])
    return skill.release_revision


def _validated_names(names: list[str]) -> list[str]:
    if (
        not isinstance(names, list)
        or len(names) > MAX_LABELS
        or any(
            not isinstance(name, str)
            or not re.fullmatch(LABEL_RE, name)
            or name in RESERVED_LABELS
            for name in names
        )
        or len(set(names)) != len(names)
    ):
        raise SkillsHubError(
            "invalid_label",
            "Use up to 20 unique lowercase label names other than latest.",
            status=400,
        )
    return sorted(names)


@transaction.atomic
def set_label_protection(
    *,
    principal: SkillPrincipal,
    skill_id: UUID,
    protected_label_names: list[str],
    expected_release_revision: int,
) -> dict:
    """CAS policy and append-only audit event commit in the same transaction."""
    if principal.kind == "system_key":
        raise SkillsHubError(
            "principal_not_supported",
            "System keys cannot change protection.",
            status=403,
        )
    require_scope_access(principal=principal, write=True)
    try:
        skill = HubSkill.no_workspace_objects.select_for_update(of=("self",)).get(
            pk=skill_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            workspace__organization_id=principal.scope.organization_id,
        )
    except HubSkill.DoesNotExist as exc:
        raise SkillsHubError(
            "resource_not_found", "Skill not found.", status=404
        ) from exc
    _require_admin(principal=principal, skill=skill, code="label_protection_forbidden")
    names = _validated_names(protected_label_names)
    _check_revision(skill=skill, expected=expected_release_revision, required=True)
    old_names = list(skill.protected_label_names)
    if set(old_names) != set(names):
        previous = skill.release_revision
        skill.protected_label_names = names
        skill.save(update_fields=["protected_label_names"])
        revision = advance_release_revision_locked(skill=skill)
        HubSkillLabelPolicyEvent.objects.create(
            organization_id=skill.organization_id,
            workspace_id=skill.workspace_id,
            skill=skill,
            actor_id=principal.user_id,
            actor_api_key_id=principal.api_key_id,
            old_names=old_names,
            new_names=names,
            from_revision=previous,
            to_revision=revision,
        )
    return {
        "protected_label_names": skill.protected_label_names,
        "release_revision": skill.release_revision,
        "can_manage": True,
    }
