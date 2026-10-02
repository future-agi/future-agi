"""Seed the ``cluster-rca`` builtin Falcon skill.

``builtin_skills/cluster-rca.yaml`` was added after 0007 (which seeded the
original builtins), and the post_migrate sync that 0007's docstring references
was never actually wired up in ``falcon_ai/apps.py`` — so new YAML skills never
reach the DB. With the skill absent, the "Analyze cluster" delegation
(``AgentLoop.run`` → ``run_cluster_rca``, gated on
``skill.slug == "cluster-rca"``) silently no-ops and the Analyze tab hangs.

Re-run the idempotent YAML seeder (``update_or_create`` on
slug + is_builtin=True + organization=NULL) to pick up ``cluster-rca`` (and
resync any other builtins). Safe to re-run; reverse is a no-op.
"""

from django.db import migrations


def seed_cluster_rca(apps, schema_editor):
    Skill = apps.get_model("falcon_ai", "Skill")
    from ee.falcon_ai.builtin_skills_loader import seed_builtin_skills

    seed_builtin_skills(skill_model=Skill)


def reverse_noop(apps, schema_editor):
    # Leave seeded builtins in place — they're shared global rows; tearing one
    # out could orphan Conversation.active_skill FKs.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("falcon_ai", "0007_global_builtin_skills"),
    ]

    operations = [
        migrations.RunPython(seed_cluster_rca, reverse_noop),
    ]
