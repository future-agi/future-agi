from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0085_hostedharnessscenario_dataset_row"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="diagnostics_captured_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="diagnostics_error",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="diagnostics_final",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="diagnostics_object_key",
            field=models.CharField(blank=True, max_length=1024, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="diagnostics_sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="hostedharnessattempt",
            name="diagnostics_size",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
