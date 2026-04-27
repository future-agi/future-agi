from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0071_observation_span_eval_attributes_gin"),
    ]

    operations = [
        migrations.AlterField(
            model_name="savedview",
            name="tab_type",
            field=models.CharField(
                choices=[
                    ("traces", "Traces"),
                    ("spans", "Spans"),
                    ("voice", "Voice"),
                    ("imagine", "Imagine"),
                    ("users", "Users"),
                    ("user_detail", "User Detail"),
                    ("sessions", "Sessions"),
                ],
                max_length=20,
            ),
        ),
    ]
