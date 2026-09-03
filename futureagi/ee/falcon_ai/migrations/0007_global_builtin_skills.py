"""Make builtin Falcon skills global (one row per slug, organization=NULL).

Steps:
  1. Make ``Skill.organization`` nullable.
  2. Add a partial unique constraint so there's only one global row per slug.
  3. Consolidate any existing per-org builtin rows into a single global row
     per slug, repointing Conversation.active_skill FKs so nothing orphans.
  4. Seed the YAML-defined builtins into the DB.

The post_migrate signal in ``falcon_ai/apps.py`` keeps YAML edits in sync
going forward — this migration only handles the schema transition and the
one-time dedupe.
"""

from django.db import migrations, models


def consolidate_and_seed(apps, schema_editor):
    Skill = apps.get_model("falcon_ai", "Skill")
    Conversation = apps.get_model("falcon_ai", "Conversation")

    from ee.falcon_ai.builtin_skills_loader import (
        consolidate_per_org_builtins,
        seed_builtin_skills,
    )

    consolidate_per_org_builtins(Skill, Conversation)
    seed_builtin_skills(skill_model=Skill)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("falcon_ai", "0006_add_context_compaction"),
        ("accounts", "0019_merge_20260407_1927"),
    ]

    operations = [
        migrations.AlterField(
            model_name="skill",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="falcon_skills",
                to="accounts.organization",
            ),
        ),
        migrations.AddConstraint(
            model_name="skill",
            constraint=models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(organization__isnull=True),
                name="unique_global_skill_slug",
            ),
        ),
        migrations.RunPython(consolidate_and_seed, reverse_noop),
    ]
