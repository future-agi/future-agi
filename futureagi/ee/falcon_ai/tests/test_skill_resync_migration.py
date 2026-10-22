"""Migration 0009 retires builtin skills whose YAML was deleted.

A fresh database never reproduces the bug this guards: 0007/0008 seed from the
YAML as it stands, so a slug that was removed simply never gets created. The
failure only exists on installs that seeded the skill *before* it was deleted —
cloud and every long-lived environment. So drive the migration's own function
against a row in that state.
"""

import importlib

import pytest

from ee.falcon_ai.models import Skill

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "ee.falcon_ai.migrations.0009_resync_builtin_skills"
)


class _Apps:
    """Stand-in for the migration's `apps`, returning the live Skill model."""

    def get_model(self, app_label, model_name):
        assert (app_label, model_name) == ("falcon_ai", "Skill")
        return Skill


def test_builtin_whose_yaml_was_deleted_is_deactivated():
    Skill.objects.create(
        organization=None,
        slug="localize-errors",
        name="Localize Errors",
        description="Removed from the repo but still live in this database.",
        icon="mdi:crosshairs",
        instructions="…",
        tool_names=["trigger_error_localization", "get_error_localization_status"],
        is_builtin=True,
        is_active=True,
        workspace=None,
    )

    _migration.resync_builtin_skills(_Apps(), None)

    retired = Skill.objects.get(slug="localize-errors", is_builtin=True)
    # Deactivated, not deleted: Conversation.active_skill points at these rows.
    assert retired.is_active is False
    assert Skill.objects.filter(slug="localize-errors").exists()


def test_workspace_skills_are_never_touched():
    mine = Skill.objects.create(
        organization=None,
        slug="my-team-skill",
        name="My Team Skill",
        description="Authored in the product, not shipped in the repo.",
        icon="mdi:star",
        instructions="…",
        tool_names=["list_datasets"],
        is_builtin=False,
        is_active=True,
        workspace=None,
    )

    _migration.resync_builtin_skills(_Apps(), None)

    mine.refresh_from_db()
    assert mine.is_active is True


def test_surviving_builtins_match_the_yaml_tool_lists():
    from ai_tools.registry import registry

    _migration.resync_builtin_skills(_Apps(), None)

    stale = {
        skill.slug: [n for n in skill.tool_names if registry.get(n) is None]
        for skill in Skill.objects.filter(
            is_builtin=True, organization__isnull=True, is_active=True
        )
    }
    assert {slug: names for slug, names in stale.items() if names} == {}


def test_cost_skill_resync_removes_the_cloud_only_dependency():
    skill, _ = Skill.objects.update_or_create(
        organization=None,
        slug="analyze-costs",
        is_builtin=True,
        defaults={
            "name": "Analyze Costs",
            "instructions": "Start with get_usage_overview",
            "tool_names": ["get_usage_overview"],
            "is_active": True,
            "workspace": None,
        },
    )
    _migration.resync_builtin_skills(_Apps(), None)
    _migration.resync_builtin_skills(_Apps(), None)
    skill.refresh_from_db()
    assert skill.is_active
    assert "get_usage_overview" not in skill.tool_names
    assert "get_usage_overview" not in skill.instructions
    assert {"list_dashboard_metrics", "query_dashboard_metrics"} <= set(
        skill.tool_names
    )
