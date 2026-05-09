"""
Add tags ArrayField to Graph and extend template validation to allow org-scoped templates.

- tags: searchable labels for template discovery (e.g. 'rag', 'safety')
- GIN index on tags for efficient containment queries (@> operator)
- Composite index (is_template, organization) for fast template-by-org lookups

No data migration needed — tags defaults to [].
"""

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agent_playground", "0011_alter_prompttemplatenode_prompt_template_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="graph",
            name="tags",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=50),
                blank=True,
                default=list,
                help_text="Searchable labels for template discovery (e.g. 'rag', 'safety', 'classification')",
                size=None,
            ),
        ),
        migrations.AddIndex(
            model_name="graph",
            index=models.Index(
                fields=["is_template", "organization"],
                name="agentpg_graph_tpl_org_idx",
            ),
        ),
    ]
