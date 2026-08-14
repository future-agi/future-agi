from django.core.validators import MinLengthValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0105_optimizedataset_workspace_scope"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tools",
            name="name",
            field=models.CharField(max_length=255, validators=[MinLengthValidator(1)]),
        ),
        migrations.AddConstraint(
            model_name="tools",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted", False)),
                fields=("organization", "workspace", "name"),
                name="unique_active_tool_name_workspace",
            ),
        ),
    ]
