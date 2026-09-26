"""Loader for builtin Falcon skills.

Skill definitions live as YAML files in ``falcon_ai/builtin_skills/``. On every
``migrate`` this module syncs them into the DB as global (organization=NULL)
rows, creating anything new and updating existing rows so edits to a YAML
propagate forward without needing a new migration.

This is intentionally lightweight — one row per skill, no tracking table.
Idempotency comes from ``update_or_create`` keyed on slug + ``is_builtin=True``
+ ``organization__isnull=True``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "builtin_skills"

REQUIRED_FIELDS = {"slug", "name", "description", "instructions"}
OPTIONAL_FIELDS_WITH_DEFAULTS: dict[str, Any] = {
    "icon": "mdi:star",
    "tool_names": [],
    "trigger_phrases": [],
    "example_trajectories": [],
}


def load_builtin_skills() -> list[dict[str, Any]]:
    """Read every ``*.yaml`` under SKILLS_DIR and return the parsed list.

    Skips non-YAML files silently. Raises on malformed or incomplete files
    because a broken YAML is a bug that should fail loudly at deploy time.
    """
    if not SKILLS_DIR.is_dir():
        return []

    skills: list[dict[str, Any]] = []
    for path in sorted(SKILLS_DIR.glob("*.yaml")):
        with path.open() as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected top-level mapping, got {type(data)}")
        missing = REQUIRED_FIELDS - data.keys()
        if missing:
            raise ValueError(f"{path}: missing required fields {sorted(missing)}")
        for field, default in OPTIONAL_FIELDS_WITH_DEFAULTS.items():
            data.setdefault(field, default)
        skills.append(data)
    return skills


def seed_builtin_skills(skill_model=None, conversation_model=None) -> tuple[int, int]:
    """Insert-or-update every YAML-defined skill as a global builtin.

    Safe to run at any time — uses update_or_create on (slug, is_builtin=True,
    organization=NULL). Returns (created_count, updated_count).

    ``skill_model`` / ``conversation_model`` let callers (e.g. data migrations)
    inject the historical model from ``apps.get_model``. If omitted, the live
    models are used — appropriate for the management command and the
    post_migrate signal.
    """
    if skill_model is None:
        from ee.falcon_ai.models import Skill as skill_model  # noqa: N813

    created = 0
    updated = 0

    for skill_data in load_builtin_skills():
        slug = skill_data["slug"]
        defaults = {
            "name": skill_data["name"],
            "description": skill_data["description"],
            "icon": skill_data["icon"],
            "instructions": skill_data["instructions"],
            "tool_names": skill_data["tool_names"],
            "example_trajectories": skill_data["example_trajectories"],
            "trigger_phrases": skill_data["trigger_phrases"],
            "is_builtin": True,
            "is_active": True,
            "workspace": None,
        }

        existing = skill_model.objects.filter(
            slug=slug, is_builtin=True, organization__isnull=True
        ).first()

        if existing:
            dirty = False
            for field, value in defaults.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    dirty = True
            if dirty:
                existing.save()
                updated += 1
        else:
            skill_model.objects.create(
                organization=None, slug=slug, **defaults
            )
            created += 1

    return created, updated


def consolidate_per_org_builtins(
    skill_model, conversation_model
) -> int:
    """One-time dedupe for installs that seeded per-org builtins previously.

    For each builtin slug, promotes the oldest existing row to global
    (organization=NULL), repoints Conversation.active_skill FKs from the
    discarded rows, and deletes them. Returns the number of rows deleted.

    Requires historical models (pass ``apps.get_model``-returned classes).
    Callable from data migrations only — don't import into app code.
    """
    deleted = 0
    slugs = (
        skill_model.objects.filter(is_builtin=True)
        .values_list("slug", flat=True)
        .distinct()
    )

    for slug in slugs:
        rows = list(
            skill_model.objects.filter(slug=slug, is_builtin=True).order_by(
                "created_at"
            )
        )
        if not rows:
            continue
        canonical = rows[0]
        if canonical.organization_id is not None:
            canonical.organization = None
            canonical.save(update_fields=["organization"])

        other_ids = [r.id for r in rows[1:]]
        if other_ids:
            conversation_model.objects.filter(
                active_skill_id__in=other_ids
            ).update(active_skill=canonical)
            skill_model.objects.filter(id__in=other_ids).delete()
            deleted += len(other_ids)

    return deleted
