"""Resync builtin Falcon skills after the MCP catalog tool rename.

Falcon reads skills from DB rows, not from the YAML on disk. The YAML is only
copied into the database by a data migration — there is still no post_migrate
hook (see 0008's docstring) — so rewriting ``builtin_skills/*.yaml`` leaves
every existing install, cloud included, running the previous definitions.

Two things are stale after the catalog change:

* ``tool_names`` on the rewritten skills still name tools that were removed
  (``fix-with-falcon`` alone referenced 10 that no longer resolve, e.g.
  ``get_span_tree``, ``trigger_error_localization``, ``get_session_analytics``).
  Falcon hands those names to the model, and every call fails.
* ``localize-errors.yaml`` was deleted, but ``seed_builtin_skills`` only
  upserts the slugs it finds on disk — it never retires one that disappeared —
  so the skill stays selectable with a tool list that cannot run.

Re-run the idempotent seeder for the first, then deactivate any builtin whose
slug is no longer on disk for the second. Deactivate rather than delete:
``Conversation.active_skill`` points at these rows, and 0007/0008 deliberately
leave them in place for that reason.
"""

from django.db import migrations


def resync_builtin_skills(apps, schema_editor):
    Skill = apps.get_model("falcon_ai", "Skill")
    from ee.falcon_ai.builtin_skills_loader import (
        load_builtin_skills,
        seed_builtin_skills,
    )

    # 1. Refresh tool_names/instructions for every slug still defined on disk.
    seed_builtin_skills(skill_model=Skill)

    # 2. Retire builtins whose YAML is gone. Scoped to is_builtin=True and
    #    organization=NULL so workspace-authored skills are never touched.
    on_disk = {skill["slug"] for skill in load_builtin_skills()}
    Skill.objects.filter(
        is_builtin=True, organization__isnull=True, is_active=True
    ).exclude(slug__in=on_disk).update(is_active=False)


def reverse_noop(apps, schema_editor):
    # Same reasoning as 0007/0008: these are shared global rows and
    # Conversation.active_skill references them, so nothing is torn out.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("falcon_ai", "0008_seed_cluster_rca_skill"),
    ]

    operations = [
        migrations.RunPython(resync_builtin_skills, reverse_noop),
    ]
