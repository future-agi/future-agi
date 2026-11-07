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


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not re.fullmatch(LABEL_RE, name) or name in RESERVED_LABELS:
        raise SkillsHubError(
            "invalid_label", "Use a lowercase label name other than latest.", status=400
        )


def _check_expected(current: HubSkillLabel | None, expected_version: object) -> None:
    current_number = current.version.number if current else None
    if expected_version is not MISSING and expected_version != current_number:
        raise SkillsHubError(
            "label_conflict", "This label changed. Reload it before trying again.",
            status=409, detail={"current_version": current_number},
        )


@transaction.atomic
def set_label(
    *, skill: HubSkill, actor: Actor, name: str, version_number: int,
    expected_version: object = MISSING, note: str = "",
) -> HubSkillLabel:
    """Move one pointer with optional compare-and-swap, recording the change."""
    _validate_name(name)
    if len(note) > 500:
        raise SkillsHubError("payload_too_large", "Label notes can have up to 500 characters.", status=413)
    skill = scoped_skill(skill, lock=True)
    current = skill.labels.select_related("version").filter(name=name).first()
    _check_expected(current, expected_version)
    version = skill.versions.filter(number=version_number).first()
    if version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    if current is None and skill.labels.count() >= MAX_LABELS:
        raise SkillsHubError("invalid_label", "A skill can have up to 20 labels.", status=400)
    previous = current.version.number if current else None
    if previous == version_number:
        return current
    if current is None:
        current = HubSkillLabel(skill=skill, name=name)
    current.version = version
    current.updated_by = actor.user
    current.updated_by_api_key = actor.api_key
    current.save()
    HubSkillLabelEvent.objects.create(
        skill=skill, label=name, from_version=previous, to_version=version_number,
        actor=actor.user, actor_api_key=actor.api_key, note=note,
    )
    skill.updated_by = actor.user
    skill.updated_at = timezone.now()
    skill.save(update_fields=["updated_by", "updated_at"])
    return current


@transaction.atomic
def remove_label(
    *, skill: HubSkill, actor: Actor, name: str,
    expected_version: object = MISSING, note: str = "",
) -> None:
    """Remove a pointer with the same compare-and-swap semantics as promotion."""
    _validate_name(name)
    if len(note) > 500:
        raise SkillsHubError("payload_too_large", "Label notes can have up to 500 characters.", status=413)
    skill = scoped_skill(skill, lock=True)
    current = skill.labels.select_related("version").filter(name=name).first()
    _check_expected(current, expected_version)
    if current is None:
        raise SkillsHubError("label_not_found", "Label not found.", status=404)
    previous = current.version.number
    current.delete()
    HubSkillLabelEvent.objects.create(
        skill=skill, label=name, from_version=previous, to_version=None,
        actor=actor.user, actor_api_key=actor.api_key, note=note,
    )
    skill.updated_by = actor.user
    skill.save(update_fields=["updated_by", "updated_at"])


def label_history(*, skill: HubSkill, label: str | None = None) -> QuerySet[HubSkillLabelEvent]:
    """Read history newest first without requiring the licensed audit feature."""
    skill = scoped_skill(skill)
    queryset = skill.label_events.select_related("actor", "actor_api_key")
    if label is not None:
        queryset = queryset.filter(label=label)
    return queryset.order_by("-created_at", "-id")
