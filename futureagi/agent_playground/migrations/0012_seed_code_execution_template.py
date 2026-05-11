from django.db import migrations


def seed_code_execution_template(apps, schema_editor):
    from agent_playground.templates.code_execution import CODE_EXECUTION_TEMPLATE

    NodeTemplate = apps.get_model("agent_playground", "NodeTemplate")
    defaults = {k: v for k, v in CODE_EXECUTION_TEMPLATE.items() if k != "name"}
    NodeTemplate.objects.update_or_create(
        name=CODE_EXECUTION_TEMPLATE["name"],
        defaults=defaults,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("agent_playground", "0011_alter_prompttemplatenode_prompt_template_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_code_execution_template, migrations.RunPython.noop),
    ]
