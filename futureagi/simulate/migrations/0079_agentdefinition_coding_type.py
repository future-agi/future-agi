from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('simulate', '0078_agentdefinition_target_speaks_first'),
    ]

    operations = [
        migrations.AlterField(
            model_name='agentdefinition',
            name='agent_type',
            field=models.CharField(
                choices=[('voice', 'Voice'), ('text', 'Text'), ('coding', 'Coding')],
                default='voice',
                max_length=255,
            ),
        ),
    ]
