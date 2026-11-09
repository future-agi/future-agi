"""Transactional label pointers and their durable, OSS promotion history."""

from __future__ import annotations

import re

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill, HubSkillLabel, HubSkillLabelEvent
from model_hub.selectors.skills_hub import scoped_skill
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.limits import LABEL_RE, MAX_LABELS, RESERVED_LABELS
from model_hub.services.skills_hub.types import MISSING, Actor
from model_hub.services.skills_hub.v11.evidence import (
    attach_test_evidence_locked,
    validate_test_evidence_locked,
)
from model_hub.services.skills_hub.v11.governance import (
    advance_release_revision_locked,
    authorize_label_change_locked,
    principal_for_actor,
)


def _validate_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or not re.fullmatch(LABEL_RE, name)
        or name in RESERVED_LABELS
    ):
        raise SkillsHubError(
            "invalid_label", "Use a lowercase label name other than latest.", status=400
        )


def _check_expected(current: HubSkillLabel | None, expected_version: object) -> None:
    current_number = current.version.number if current else None
    if expected_version is not MISSING and expected_version != current_number:
        raise SkillsHubError(
            "label_conflict",
            "This label changed. Reload it before trying again.",
            status=409,
            detail={"current_version": current_number},
        )


@transaction.atomic
def set_label(
    *,
    skill: HubSkill,
    actor: Actor,
    name: str,
    version_number: int,
    expected_version: object = MISSING,
    note: str = "",
    expected_release_revision: object = MISSING,
    expected_digest: str | None = None,
    evidence_run_id=None,
) -> HubSkillLabel:
    """Move one pointer with optional compare-and-swap, recording the change."""
    _validate_name(name)
    if len(note) > 500:
        raise SkillsHubError(
            "payload_too_large",
            "Label notes can have up to 500 characters.",
            status=413,
        )
    skill = scoped_skill(skill, lock=True)
    principal = principal_for_actor(skill=skill, actor=actor)
    authorize_label_change_locked(
        principal=principal,
        skill=skill,
        name=name,
        expected_version=expected_version,
        expected_release_revision=expected_release_revision,
    )
    return _set_label_locked(
        skill=skill,
        actor=actor,
        name=name,
        version_number=version_number,
        note=note,
        expected_digest=expected_digest,
        evidence_run_id=evidence_run_id,
    )


def _set_label_locked(
    *,
    skill: HubSkill,
    actor: Actor,
    name: str,
    version_number: int,
    note="",
    expected_digest=None,
    evidence_run_id=None,
    evidence_currentness: dict | None = None,
) -> HubSkillLabel:
    """Private mutation seam: caller holds the skill lock and has authorized all moves."""
    _validate_name(name)
    current = skill.labels.select_related("version").filter(name=name).first()
    version = skill.versions.filter(number=version_number).first()
    if version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    from model_hub.services.skills_hub.v12.resolution_leases import (
        require_distribution_eligibility,
    )

    require_distribution_eligibility(version)
    if expected_digest is not None and expected_digest != version.digest:
        raise SkillsHubError(
            "candidate_changed", "The reviewed content changed.", status=409
        )
    principal = principal_for_actor(skill=skill, actor=actor)
    if current is None and skill.labels.count() >= MAX_LABELS:
        raise SkillsHubError(
            "invalid_label", "A skill can have up to 20 labels.", status=400
        )
    freshness = evidence_currentness
    if evidence_run_id is not None:
        _, observed = validate_test_evidence_locked(
            principal=principal,
            skill=skill,
            candidate_digest=version.digest,
            run_id=evidence_run_id,
        )
        if freshness is None:
            freshness = observed
    previous = current.version.number if current else None
    if current is not None and previous == version_number:
        if evidence_run_id is not None:
            attach_test_evidence_locked(
                principal=principal,
                skill=skill,
                version=version,
                run_id=evidence_run_id,
                currentness_at_action=freshness,
            )
        current.skill = skill
        return current
    if current is None:
        current = HubSkillLabel(skill=skill, name=name)
    current.version = version
    current.updated_by = actor.user
    current.updated_by_api_key = actor.api_key
    current.save()
    event = HubSkillLabelEvent.objects.create(
        skill=skill,
        label=name,
        from_version=previous,
        to_version=version_number,
        release_revision=advance_release_revision_locked(skill=skill),
        actor=actor.user,
        actor_api_key=actor.api_key,
        note=note,
    )
    if evidence_run_id is not None:
        attach_test_evidence_locked(
            principal=principal,
            skill=skill,
            version=version,
            run_id=evidence_run_id,
            label_event=event,
            currentness_at_action=freshness,
        )
    current.skill = skill
    skill.updated_by = actor.user
    skill.updated_at = timezone.now()
    skill.save(update_fields=["updated_by", "updated_at"])
    return current


@transaction.atomic
def remove_label(
    *,
    skill: HubSkill,
    actor: Actor,
    name: str,
    expected_version: object = MISSING,
    note: str = "",
    expected_release_revision: object = MISSING,
) -> None:
    """Remove a pointer with the same compare-and-swap semantics as promotion."""
    _validate_name(name)
    if len(note) > 500:
        raise SkillsHubError(
            "payload_too_large",
            "Label notes can have up to 500 characters.",
            status=413,
        )
    skill = scoped_skill(skill, lock=True)
    authorize_label_change_locked(
        principal=principal_for_actor(skill=skill, actor=actor),
        skill=skill,
        name=name,
        expected_version=expected_version,
        expected_release_revision=expected_release_revision,
    )
    current = skill.labels.select_related("version").filter(name=name).first()
    if current is None:
        raise SkillsHubError("label_not_found", "Label not found.", status=404)
    previous = current.version.number
    current.delete()
    HubSkillLabelEvent.objects.create(
        skill=skill,
        label=name,
        from_version=previous,
        to_version=None,
        release_revision=advance_release_revision_locked(skill=skill),
        actor=actor.user,
        actor_api_key=actor.api_key,
        note=note,
    )
    skill.updated_by = actor.user
    skill.save(update_fields=["updated_by", "updated_at"])


def label_history(
    *, skill: HubSkill, label: str | None = None
) -> QuerySet[HubSkillLabelEvent]:
    """Read history newest first without requiring the licensed audit feature."""
    skill = scoped_skill(skill)
    queryset = skill.label_events.select_related("actor", "actor_api_key")
    if label is not None:
        queryset = queryset.filter(label=label)
    return queryset.order_by("-created_at", "-id")
